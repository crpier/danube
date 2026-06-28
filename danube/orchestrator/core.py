"""Master Core: drive a job through its lifecycle against a `Runner` and the DB.

`JobOrchestrator` ties a `Runner`, a `ControlPlane`, and a snekql `Database`
together. It persists a job, asks the runner to create its environment, opens a
control-plane session, starts the Coordinator, and waits for it to drive the
pipeline to completion — advancing the job through the lifecycle in
`danube.domain.lifecycle` so illegal transitions are impossible.

The orchestrator does not run steps itself. The Coordinator drives each
`step.run()` over the RPC control plane (`docs/architecture/execution-model.md`,
Execution Flow); the `ControlPlane` execs the command in the Worker, scrubs and
logs the output, and records the step. The orchestrator owns only the
environment/coordinator lifecycle around that loop.

Failure handling follows `docs/architecture/execution-model.md`:

- the runner failing to create the environment ends the job in `failure` before
  it ever reaches `running`;
- the Coordinator reporting `success`/`failure` over `/rpc/report-status` is the
  authoritative outcome; the orchestrator does not overwrite a terminal state;
- a Coordinator that exits non-zero without reporting (a crash) ends the job in
  `failure`;
- exceeding the pipeline's `max_duration_seconds` stops the job and ends it in
  `timeout`;
- a runner error while starting/awaiting the Coordinator ends the job in
  `failure`.

The control-plane session is closed and `runner.cleanup_job` is called once the
environment exists, on every path.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import anyio
from snekql.sqlite import Database, Fetched, insert, select, update

from danube.db.models import Job, Pipeline
from danube.domain.enums import JobStatus, TriggerType
from danube.domain.lifecycle import TERMINAL_STATES, transition
from danube.domain.runner_types import CoordinatorExit, JobHandle, StartJobRequest
from danube.runner.base import Runner
from danube.sdk.client import ENV_JOB_ID, ENV_RPC_ADDRESS, ENV_RPC_TOKEN

if TYPE_CHECKING:
    # Imported for typing only: at runtime `danube.rpc.control_plane` imports
    # `danube.orchestrator.log_writer`, so a runtime import here would be circular.
    from danube.rpc.control_plane import ControlPlane

logger = logging.getLogger("danube.orchestrator")


def _now() -> datetime:
    return datetime.now(UTC)


class JobOrchestrator:
    """Persist and run jobs against a `Runner`, a `ControlPlane`, and a database."""

    def __init__(
        self,
        runner: Runner,
        db: Database,
        data_dir: Path | str,
        control_plane: ControlPlane,
        *,
        rpc_address: str,
    ) -> None:
        self._runner = runner
        self._db = db
        self._data_dir = Path(data_dir)
        self._control_plane = control_plane
        self._rpc_address = rpc_address

    async def create_job(
        self,
        pipeline_id: str,
        trigger_type: TriggerType,
        trigger_ref: str | None = None,
    ) -> Job[Fetched]:
        """Persist a new job in `pending` and return the stored row."""
        job_id = str(uuid.uuid4())
        async with self._db.transaction() as tx:
            await tx.execute(
                insert(
                    Job(
                        id=job_id,
                        pipeline_id=pipeline_id,
                        trigger_type=trigger_type,
                        trigger_ref=trigger_ref,
                        status=JobStatus.PENDING,
                    )
                )
            )
            return await tx.fetch_one(select(Job).where(Job.id.eq(job_id)))

    async def run_job(self, job_id: str) -> Job[Fetched]:
        """Run ``job_id`` to a terminal state via its Coordinator.

        The job moves
        ``pending -> scheduling -> running -> {success | failure | timeout}``.
        The Coordinator drives steps over the RPC control plane; the orchestrator
        starts it, waits for it under the pipeline timeout, and finalizes the job
        unless the Coordinator already reported a terminal status. The session is
        closed and `runner.cleanup_job` runs once the environment exists, on every
        path.
        """
        job = await self._fetch_job(job_id)
        pipeline = await self._fetch_pipeline(job.pipeline_id)

        await self._advance(job_id, JobStatus.SCHEDULING)
        try:
            handle = await self._runner.start_job(
                StartJobRequest(
                    job_id=job_id,
                    pipeline_id=job.pipeline_id,
                    worker_image=pipeline.worker_image,
                    max_duration_seconds=pipeline.max_duration_seconds,
                )
            )
        except Exception as e:
            # No environment exists yet, so there is nothing to clean up here;
            # the reaper later reconciles any stale runtime state.
            logger.warning(
                "job %s failed to start: runner could not create environment: %s",
                job_id,
                e,
            )
            reason = f"runner failed to create job environment: {e}"
            return await self._finalize(job_id, JobStatus.FAILURE, reason)

        log_path = self._log_path(job_id)
        session = await self._control_plane.open_session(
            job_id=job_id, pipeline_id=job.pipeline_id, handle=handle
        )
        await self._record_scheduled(job_id, handle, log_path)
        await self._advance(job_id, JobStatus.RUNNING)

        coordinator_env = {
            ENV_RPC_ADDRESS: self._rpc_address,
            ENV_JOB_ID: job_id,
            ENV_RPC_TOKEN: session.token,
        }
        final = JobStatus.SUCCESS
        reason: str | None = None
        try:
            with anyio.fail_after(pipeline.max_duration_seconds):
                await self._runner.start_coordinator(handle, coordinator_env)
                exit_info = await self._runner.wait_for_coordinator(handle)
            final, reason = self._outcome(exit_info)
        except TimeoutError:
            final = JobStatus.TIMEOUT
            reason = (
                f"job exceeded max_duration_seconds={pipeline.max_duration_seconds}"
            )
            logger.warning("job %s timed out: %s", job_id, reason)
            await self._runner.stop_job(handle, reason="timeout")
        except Exception as e:  # runner failure starting/awaiting the Coordinator
            final = JobStatus.FAILURE
            reason = f"runner failure: {e}"
            logger.warning("job %s failed: %s", job_id, reason)
        finally:
            await self._control_plane.close_session(job_id)
            await self._runner.cleanup_job(handle)

        return await self._finalize_unless_terminal(job_id, final, reason)

    def _outcome(self, exit_info: CoordinatorExit) -> tuple[JobStatus, str | None]:
        """Map a Coordinator exit to a job outcome.

        A clean exit is `success`; a non-zero exit is a crash and ends the job in
        `failure`. When the Coordinator already reported a terminal status over
        RPC, `_finalize_unless_terminal` keeps that instead of this outcome.
        """
        if exit_info.exit_code == 0:
            return JobStatus.SUCCESS, None
        return (
            JobStatus.FAILURE,
            f"coordinator exited with code {exit_info.exit_code}",
        )

    async def _fetch_job(self, job_id: str) -> Job[Fetched]:
        async with self._db.transaction() as tx:
            return await tx.fetch_one(select(Job).where(Job.id.eq(job_id)))

    async def _fetch_pipeline(self, pipeline_id: str) -> Pipeline[Fetched]:
        async with self._db.transaction() as tx:
            return await tx.fetch_one(
                select(Pipeline).where(Pipeline.id.eq(pipeline_id))
            )

    def _log_path(self, job_id: str) -> Path:
        return self._data_dir / "logs" / f"{job_id}.log"

    async def _advance(self, job_id: str, target: JobStatus) -> None:
        """Move the job to ``target``, validating via the lifecycle state machine.

        The current state is read back from the DB so the only legal transitions
        are the ones `danube.domain.lifecycle` permits.
        """
        async with self._db.transaction() as tx:
            job = await tx.fetch_one(select(Job).where(Job.id.eq(job_id)))
            new = transition(JobStatus(job.status), target)
            _ = await tx.execute(
                update(Job).set(Job.status.to(new)).where(Job.id.eq(job_id))
            )

    async def _record_scheduled(
        self, job_id: str, handle: JobHandle, log_path: Path
    ) -> None:
        async with self._db.transaction() as tx:
            _ = await tx.execute(
                update(Job)
                .set(
                    Job.runner_id.to(handle.pod_id),
                    Job.workspace_path.to(handle.workspace_path),
                    Job.log_path.to(str(log_path)),
                    Job.started_at.to(_now()),
                )
                .where(Job.id.eq(job_id))
            )

    async def _finalize_unless_terminal(
        self, job_id: str, target: JobStatus, reason: str | None
    ) -> Job[Fetched]:
        """Finalize the job, unless the Coordinator already drove it terminal.

        The Coordinator's `/rpc/report-status` is authoritative: if it already
        moved the job to a terminal state, that result stands and this is a no-op
        read-back (avoiding an illegal terminal->terminal transition).
        """
        job = await self._fetch_job(job_id)
        if JobStatus(job.status) in TERMINAL_STATES:
            return job
        return await self._finalize(job_id, target, reason)

    async def _finalize(
        self, job_id: str, target: JobStatus, reason: str | None
    ) -> Job[Fetched]:
        async with self._db.transaction() as tx:
            job = await tx.fetch_one(select(Job).where(Job.id.eq(job_id)))
            new = transition(JobStatus(job.status), target)
            query = update(Job).set(
                Job.status.to(new),
                Job.finished_at.to(_now()),
                Job.failure_reason.to(reason),
            )
            _ = await tx.execute(query.where(Job.id.eq(job_id)))
            return await tx.fetch_one(select(Job).where(Job.id.eq(job_id)))
