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
import time
import uuid
from collections.abc import Awaitable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import anyio
from snekql.sqlite import (
    Database,
    Fetched,
    NoResultError,
    insert,
    select,
    update,
)

from danube.db.models import Job, Pipeline
from danube.domain.enums import JobStatus, TriggerType
from danube.domain.lifecycle import ALLOWED_TRANSITIONS, TERMINAL_STATES, transition
from danube.domain.runner_types import CoordinatorExit, JobHandle, StartJobRequest
from danube.observability import Metrics, Tracer, job_log_fields
from danube.runner.base import Runner
from danube.sdk.client import ENV_JOB_ID, ENV_RPC_ADDRESS, ENV_RPC_TOKEN

if TYPE_CHECKING:
    # Imported for typing only: at runtime `danube.rpc.control_plane` imports
    # `danube.orchestrator.log_writer`, so a runtime import here would be circular.
    from danube.rpc.control_plane import ControlPlane

logger = logging.getLogger("danube.orchestrator")


def _now() -> datetime:
    return datetime.now(UTC)


class PipelineNotFoundError(Exception):
    """Raised when a job is triggered for a pipeline that does not exist."""

    def __init__(self, pipeline_id: str) -> None:
        self.pipeline_id = pipeline_id
        super().__init__(f"pipeline {pipeline_id!r} not found")


class JobNotFoundError(Exception):
    """Raised when an operation references a job id that does not exist."""

    def __init__(self, job_id: str) -> None:
        self.job_id = job_id
        super().__init__(f"job {job_id!r} not found")


class CannotCancelError(Exception):
    """Raised when a cancel is requested for a job that is not currently running.

    Only a `running` job can be cancelled; a job that is still scheduling, or has
    already reached a terminal state, cannot transition to `cancelled`.
    """

    def __init__(self, job_id: str, current: JobStatus) -> None:
        self.job_id = job_id
        self.current = current
        super().__init__(f"job {job_id!r} cannot be cancelled from {current}")


@dataclass(slots=True)
class _RunningJob:
    """In-flight bookkeeping for a job `run_job` is currently driving.

    `cancel_scope` wraps the Coordinator wait; cancelling it unwinds the wait so
    the job finalizes as `cancelled`. The handle is recorded once the runner has
    created the environment so a cancellation can stop the containers.
    """

    handle: JobHandle | None = None
    cancel_scope: anyio.CancelScope | None = None
    cancel_requested: bool = False
    cancel_reason: str | None = field(default=None)


class JobOrchestrator:
    """Persist and run jobs against a `Runner`, a `ControlPlane`, and a database."""

    def __init__(  # noqa: PLR0913 - wires the runner, DB, control plane, and telemetry
        self,
        runner: Runner,
        db: Database,
        data_dir: Path | str,
        control_plane: ControlPlane,
        *,
        rpc_address: str,
        metrics: Metrics | None = None,
        tracer: Tracer | None = None,
    ) -> None:
        self._runner = runner
        self._db = db
        self._data_dir = Path(data_dir)
        self._control_plane = control_plane
        self._rpc_address = rpc_address
        self._running: dict[str, _RunningJob] = {}
        self._metrics = metrics or Metrics()
        self._tracer = tracer or Tracer()

    @property
    def db(self) -> Database:
        """The database this orchestrator persists jobs to.

        Exposed so collaborators that share the orchestrator's job store (such as
        the `Scheduler`) can query job/pipeline rows without a second handle.
        """
        return self._db

    async def create_job(
        self,
        pipeline_id: str,
        trigger_type: TriggerType,
        trigger_ref: str | None = None,
    ) -> Job[Fetched]:
        """Persist a new job in `pending` and return the stored row.

        Raises `PipelineNotFoundError` if no pipeline has `pipeline_id`, so the
        caller can reject the trigger before a job row is created.
        """
        try:
            _ = await self._fetch_pipeline(pipeline_id)
        except NoResultError:
            raise PipelineNotFoundError(pipeline_id) from None
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

        While the job runs it is registered in `self._running` so a concurrent
        `request_cancel` can unwind it; the entry is dropped on exit.
        """
        running = _RunningJob()
        self._running[job_id] = running
        self._metrics.active_jobs.inc()
        started = time.monotonic()
        try:
            with self._tracer.span(f"job {job_id}", job_id=job_id):
                job = await self._drive_job(job_id, running)
            # Recorded only on a clean return (using the terminal row `_drive_job`
            # already read back), so no DB work runs on the cancellation path that
            # the scheduler unwinds at shutdown.
            self._record_terminal_metrics(job, time.monotonic() - started)
            return job
        finally:
            _ = self._running.pop(job_id, None)
            self._metrics.active_jobs.dec()

    async def _drive_job(self, job_id: str, running: _RunningJob) -> Job[Fetched]:
        """Drive a registered job to a terminal state. See `run_job`."""
        job = await self._fetch_job(job_id)
        pipeline = await self._fetch_pipeline(job.pipeline_id)
        log_fields = job_log_fields(job)
        logger.info("job_started", extra={"event": "job_started", **log_fields})

        await self._advance(job_id, JobStatus.SCHEDULING)
        try:
            with self._tracer.span("runner start environment", job_id=job_id):
                handle = await self._timed_runner_op(
                    "start",
                    self._runner.start_job(
                        StartJobRequest(
                            job_id=job_id,
                            pipeline_id=job.pipeline_id,
                            worker_image=pipeline.worker_image,
                            max_duration_seconds=pipeline.max_duration_seconds,
                        )
                    ),
                )
        except Exception as e:
            # No environment exists yet, so there is nothing to clean up here;
            # the reaper later reconciles any stale runtime state.
            reason = f"runner failed to create job environment: {e}"
            logger.warning(
                "job_start_failed",
                extra={"event": "job_start_failed", "job_id": job_id, "reason": reason},
            )
            return await self._finalize(job_id, JobStatus.FAILURE, reason)

        self._metrics.runner_containers_active.inc()

        running.handle = handle
        log_path = self._log_path(job_id)
        session = await self._control_plane.open_session(
            job_id=job_id, pipeline_id=job.pipeline_id, handle=handle
        )
        await self._record_scheduled(job_id, handle, log_path)

        coordinator_env = {
            ENV_RPC_ADDRESS: self._rpc_address,
            ENV_JOB_ID: job_id,
            ENV_RPC_TOKEN: session.token,
        }
        final = JobStatus.SUCCESS
        reason: str | None = None
        try:
            # The cancel scope is registered before the job is marked `running` so
            # that a `request_cancel` (which only fires once the DB shows `running`)
            # always finds a live scope to cancel.
            with anyio.CancelScope() as scope:
                running.cancel_scope = scope
                await self._advance(job_id, JobStatus.RUNNING)
                with anyio.fail_after(pipeline.max_duration_seconds):
                    await self._runner.start_coordinator(handle, coordinator_env)
                    with self._tracer.span("wait for coordinator", job_id=job_id):
                        exit_info = await self._runner.wait_for_coordinator(handle)
                final, reason = self._outcome(exit_info)
            if scope.cancelled_caught:
                # Cancellation unwound the wait but did not stop the containers;
                # stop them now, before cleanup removes the environment.
                final = JobStatus.CANCELLED
                reason = running.cancel_reason or "job cancelled"
                logger.info(
                    "job_cancelled",
                    extra={
                        "event": "job_cancelled",
                        "job_id": job_id,
                        "reason": reason,
                    },
                )
                await self._runner.stop_job(handle, reason="cancelled")
        except TimeoutError:
            final = JobStatus.TIMEOUT
            reason = (
                f"job exceeded max_duration_seconds={pipeline.max_duration_seconds}"
            )
            logger.warning(
                "job_timed_out",
                extra={"event": "job_timed_out", "job_id": job_id, "reason": reason},
            )
            await self._runner.stop_job(handle, reason="timeout")
        except Exception as e:  # runner failure starting/awaiting the Coordinator
            final = JobStatus.FAILURE
            reason = f"runner failure: {e}"
            logger.warning(
                "job_failed",
                extra={"event": "job_failed", "job_id": job_id, "reason": reason},
            )
        finally:
            await self._control_plane.close_session(job_id)
            await self._cleanup(handle)

        return await self._finalize_unless_terminal(job_id, final, reason)

    async def _timed_runner_op[T](self, operation: str, awaitable: Awaitable[T]) -> T:
        """Await a runner operation, recording its duration and success/error.

        Re-raises any exception after recording the `error` outcome so the
        orchestrator's existing failure handling is unchanged.
        """
        start = time.monotonic()
        try:
            result = await awaitable
        except Exception:
            self._metrics.runner_operations_total.inc(
                operation=operation, status="error"
            )
            raise
        else:
            self._metrics.runner_operations_total.inc(
                operation=operation, status="success"
            )
            return result
        finally:
            self._metrics.runner_operation_duration_seconds.observe(
                time.monotonic() - start, operation=operation
            )

    async def _cleanup(self, handle: JobHandle) -> None:
        """Clean up the job environment, instrumenting the runner operation.

        A cleanup failure is recorded (and the container gauge still decremented)
        but re-raised so the existing finally-path semantics are preserved.
        """
        self._metrics.runner_containers_active.dec()
        try:
            with self._tracer.span("runner cleanup", job_id=handle.job_id):
                await self._timed_runner_op("cleanup", self._runner.cleanup_job(handle))
        except Exception:
            self._metrics.runner_cleanup_failures_total.inc()
            raise

    async def request_cancel(
        self, job_id: str, reason: str = "job cancelled by user"
    ) -> None:
        """Request cancellation of a running job.

        Only a `running` job can be cancelled. An unknown job raises
        `JobNotFoundError`; a job that is not currently running (still scheduling,
        or already terminal) raises `CannotCancelError`. On success the running
        job's cancel scope is cancelled, which unwinds the Coordinator wait so
        `run_job` finalizes the job as `cancelled` and cleans up.
        """
        job = await self.fetch_job(job_id)
        current = JobStatus(job.status)
        running = self._running.get(job_id)
        # `cancelled` is only a legal target from `running` per the lifecycle state
        # machine, and only a `running` job has a live cancel scope to unwind.
        if (
            JobStatus.CANCELLED not in ALLOWED_TRANSITIONS[current]
            or running is None
            or running.cancel_scope is None
        ):
            raise CannotCancelError(job_id, current)
        running.cancel_requested = True
        running.cancel_reason = reason
        running.cancel_scope.cancel()

    async def fetch_job(self, job_id: str) -> Job[Fetched]:
        """Return the job row, raising `JobNotFoundError` if it does not exist."""
        try:
            return await self._fetch_job(job_id)
        except NoResultError:
            raise JobNotFoundError(job_id) from None

    def log_path(self, job_id: str) -> Path:
        """Return the on-disk path of a job's append-only log file."""
        return self._log_path(job_id)

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

    def _record_terminal_metrics(self, job: Job[Fetched], duration: float) -> None:
        """Record terminal job metrics and emit the `job_finished` log.

        Takes the terminal job row `_drive_job` returns — which already reflects a
        Coordinator-reported status set over RPC — so no extra DB query is needed.
        """
        status = JobStatus(job.status)
        pipeline = job.pipeline_id
        self._metrics.jobs_total.inc(status=status.value, pipeline=pipeline)
        self._metrics.job_duration_seconds.observe(duration, pipeline=pipeline)
        if status is JobStatus.TIMEOUT:
            self._metrics.job_timeouts_total.inc(pipeline=pipeline)
        logger.info(
            "job_finished",
            extra={
                "event": "job_finished",
                "status": status.value,
                "duration_seconds": duration,
                **job_log_fields(job),
            },
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
