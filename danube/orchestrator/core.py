"""Master Core: drive a job through its lifecycle against a `Runner` and the DB.

`JobOrchestrator` ties a `Runner` and a snekql `Database` together. It persists a
job, asks the runner to create its environment, runs the job's steps, streams
their output through a `LogWriter`, and records per-step results — advancing the
job through the lifecycle in `danube.domain.lifecycle` so illegal transitions are
impossible.

Scope: no HTTP API, no Coordinator RPC, no scheduler. The steps to run are passed
directly to `run_job`; in production they would arrive over the Coordinator RPC
path, which lives outside this component.

Failure handling follows `docs/architecture/execution-model.md`:

- the runner failing to create the environment ends the job in `failure` before
  it ever reaches `running`;
- a step exiting non-zero aborts the remaining steps (unless `check=False`),
  ending the job in `failure`;
- a runner exception while running ends the job in `failure`;
- exceeding the pipeline's `max_duration_seconds` stops the job and ends it in
  `timeout`.

`runner.cleanup_job` is always called once the environment exists, including on
every failure path.
"""

import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import anyio
from snekql.sqlite import Database, Fetched, insert, select, update

from danube.db.models import Job, Pipeline, Step
from danube.domain.enums import JobStatus, StepStatus, TriggerType
from danube.domain.lifecycle import transition
from danube.domain.runner_types import (
    ExecResult,
    ExecStepRequest,
    JobHandle,
    StartJobRequest,
)
from danube.orchestrator.log_writer import LogWriter
from danube.runner.base import Runner

logger = logging.getLogger("danube.orchestrator")


@dataclass(frozen=True, slots=True)
class StepSpec:
    """One step the orchestrator should run, in pipeline order.

    `check=True` (the default) aborts the pipeline if the command exits non-zero;
    `check=False` mirrors `step.run(..., check=False)` and lets the pipeline
    continue regardless of exit code.
    """

    name: str
    command: str
    check: bool = True
    env: dict[str, str] = field(default_factory=dict[str, str])
    timeout_seconds: int | None = None


def _now() -> datetime:
    return datetime.now(UTC)


class JobOrchestrator:
    """Persist and run jobs against a `Runner` and a snekql `Database`."""

    def __init__(self, runner: Runner, db: Database, data_dir: Path | str) -> None:
        self._runner = runner
        self._db = db
        self._data_dir = Path(data_dir)

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

    async def run_job(self, job_id: str, steps: Sequence[StepSpec]) -> Job[Fetched]:
        """Run ``job_id``'s steps, advancing it to a terminal state.

        Returns the final job row. The job moves
        ``pending -> scheduling -> running -> {success | failure | timeout}``;
        `runner.cleanup_job` runs once the environment exists, on every path.
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
        await self._record_scheduled(job_id, handle, log_path)
        await self._advance(job_id, JobStatus.RUNNING)

        final = JobStatus.SUCCESS
        reason: str | None = None
        in_flight: str | None = None
        try:
            async with LogWriter(log_path) as log:
                with anyio.fail_after(pipeline.max_duration_seconds):
                    for sequence, spec in enumerate(steps, start=1):
                        step_id = str(uuid.uuid4())
                        in_flight = step_id
                        await self._begin_step(step_id, job_id, sequence, spec)
                        result = await self._runner.exec_step(
                            handle,
                            ExecStepRequest(
                                command=spec.command,
                                env=spec.env,
                                timeout_seconds=spec.timeout_seconds,
                            ),
                        )
                        start, end = await log.write(result.stdout + result.stderr)
                        await self._finish_step(step_id, result, start, end)
                        in_flight = None
                        if result.exit_code != 0 and spec.check:
                            final = JobStatus.FAILURE
                            reason = (
                                f"step {spec.name!r} failed with exit code "
                                f"{result.exit_code}"
                            )
                            logger.warning("job %s: %s; aborting", job_id, reason)
                            break
        except TimeoutError:
            final = JobStatus.TIMEOUT
            reason = (
                f"job exceeded max_duration_seconds={pipeline.max_duration_seconds}"
            )
            logger.warning("job %s timed out: %s", job_id, reason)
            await self._runner.stop_job(handle, reason="timeout")
            await self._fail_in_flight(in_flight)
        except Exception as e:  # runner failure: abort the job, still clean up.
            final = JobStatus.FAILURE
            reason = f"runner failure: {e}"
            logger.warning("job %s failed: %s", job_id, reason)
            await self._fail_in_flight(in_flight)
        finally:
            await self._runner.cleanup_job(handle)

        return await self._finalize(job_id, final, reason)

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

    async def _begin_step(
        self, step_id: str, job_id: str, sequence: int, spec: StepSpec
    ) -> None:
        async with self._db.transaction() as tx:
            await tx.execute(
                insert(
                    Step(
                        id=step_id,
                        job_id=job_id,
                        name=spec.name,
                        sequence=sequence,
                        command=spec.command,
                        status=StepStatus.RUNNING,
                        started_at=_now(),
                    )
                )
            )

    async def _finish_step(
        self, step_id: str, result: ExecResult, start: int, end: int
    ) -> None:
        failed = result.exit_code != 0
        status = StepStatus.FAILURE if failed else StepStatus.SUCCESS
        async with self._db.transaction() as tx:
            _ = await tx.execute(
                update(Step)
                .set(
                    Step.status.to(status),
                    Step.exit_code.to(result.exit_code),
                    Step.finished_at.to(_now()),
                    Step.log_offset_start.to(start),
                    Step.log_offset_end.to(end),
                )
                .where(Step.id.eq(step_id))
            )

    async def _fail_in_flight(self, step_id: str | None) -> None:
        """Mark a step that was still running when the job aborted as failed."""
        if step_id is None:
            return
        async with self._db.transaction() as tx:
            _ = await tx.execute(
                update(Step)
                .set(
                    Step.status.to(StepStatus.FAILURE),
                    Step.finished_at.to(_now()),
                )
                .where(Step.id.eq(step_id))
            )

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
