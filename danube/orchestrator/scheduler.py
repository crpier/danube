"""Scheduler: the single trigger/enqueue path for pipeline jobs.

`Scheduler` is the one place a pipeline run is born, whatever the source — cron,
a manual request (#19), or a webhook (#23). Every source calls `enqueue`, which
records the originating `TriggerType` on the job and applies two policies before a
job is ever handed to the orchestrator:

- **Deduplication.** At most one *active* job (`pending`/`scheduling`/`running`)
  exists per `(pipeline, ref)`. A concurrent trigger for a pipeline/ref that is
  already in flight collapses onto the existing job instead of creating a new one.
- **Concurrency.** A conservative global cap bounds how many jobs run at once
  (`docs/architecture/execution-model.md`, Concurrency). Triggers beyond the cap
  stay `pending` in the database; as running jobs finish, the scheduler dispatches
  the oldest waiting job to fill the freed slot.

Cron schedules are evaluated by `run_cron(now)` against a caller-supplied time, so
the behaviour is deterministic under test. A pipeline whose `cron_schedule`
matches the given minute enqueues exactly one run for that minute, even if the
evaluation is repeated within the same minute. A background task
(`cron_interval_seconds`, 60s by default) drives `run_cron` in production.

Background jobs run inside an `anyio` task group the scheduler owns as an async
context manager — the same pattern as `JobManager`, and for the same reason: the
task group is owned here so dispatch behaves identically under `httpx.ASGITransport`
(which skips FastAPI's lifespan) and under uvicorn.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from types import TracebackType
from typing import TYPE_CHECKING, Self

import anyio
from croniter import CroniterError, croniter
from snekql.sqlite import select

from danube.db.models import Job, Pipeline
from danube.domain.enums import JobStatus, TriggerType
from danube.domain.lifecycle import ACTIVE_STATES

if TYPE_CHECKING:
    from collections.abc import Callable

    from anyio.abc import TaskGroup
    from snekql.sqlite import Database, Fetched

    from danube.orchestrator.core import JobOrchestrator

logger = logging.getLogger("danube.scheduler")

# A conservative default: prefer leaving work queued over oversubscribing the host
# (`docs/architecture/execution-model.md`, Concurrency).
DEFAULT_MAX_CONCURRENT_JOBS = 4
DEFAULT_CRON_INTERVAL_SECONDS = 60.0

# Active (non-terminal) statuses, as a fixed tuple for `status.in_(...)` predicates.
_ACTIVE_STATUS_VALUES: tuple[JobStatus, ...] = tuple(ACTIVE_STATES)


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Scheduler:
    """Single enqueue path with dedup, a global concurrency cap, and cron."""

    def __init__(
        self,
        orchestrator: JobOrchestrator,
        db: Database,
        *,
        max_concurrent_jobs: int = DEFAULT_MAX_CONCURRENT_JOBS,
        cron_interval_seconds: float = DEFAULT_CRON_INTERVAL_SECONDS,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._orchestrator = orchestrator
        self._db = db
        self._max_concurrent_jobs = max_concurrent_jobs
        self._cron_interval_seconds = cron_interval_seconds
        self._clock = clock
        self._task_group: TaskGroup | None = None
        # Serializes the dedup-check/create and dispatch decisions so two triggers
        # racing on the same pipeline/ref cannot both create a job or oversubscribe.
        self._lock = anyio.Lock()
        # Job ids currently handed to the orchestrator; its length is the live
        # concurrency level the cap is enforced against.
        self._inflight: set[str] = set()
        # Last minute a cron pipeline fired, so repeated evaluation within a minute
        # does not enqueue twice.
        self._last_cron_fired: dict[str, datetime] = {}

    async def __aenter__(self) -> Self:
        self._task_group = anyio.create_task_group()
        _ = await self._task_group.__aenter__()
        _ = self._task_group.start_soon(self._cron_loop)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool | None:
        task_group = self._require_task_group()
        # Stop the cron loop and any still-running jobs; the reaper reconciles their
        # runtime state later.
        task_group.cancel_scope.cancel()
        try:
            return await task_group.__aexit__(exc_type, exc, tb)
        finally:
            self._task_group = None

    async def enqueue(
        self,
        pipeline_id: str,
        trigger_type: TriggerType,
        trigger_ref: str | None = None,
    ) -> Job[Fetched]:
        """Trigger a run for ``pipeline_id``, deduping and capping concurrency.

        If an active job already exists for ``(pipeline_id, trigger_ref)`` it is
        returned unchanged. Otherwise a new `pending` job is created recording
        ``trigger_type``; it is handed to the orchestrator immediately if the global
        concurrency cap allows, or left `pending` until a slot frees. Raises
        `PipelineNotFoundError` (from the orchestrator) if the pipeline is unknown.
        """
        _ = self._require_task_group()
        async with self._lock:
            existing = await self._find_active_job(pipeline_id, trigger_ref)
            if existing is not None:
                logger.info(
                    "trigger for pipeline %s ref %s deduped onto active job %s",
                    pipeline_id,
                    trigger_ref,
                    existing.id,
                )
                return existing
            job = await self._orchestrator.create_job(
                pipeline_id, trigger_type, trigger_ref
            )
            await self._dispatch()
            return job

    async def run_cron(self, now: datetime) -> list[Job[Fetched]]:
        """Evaluate every cron pipeline against ``now`` and enqueue the due ones.

        A pipeline fires when its `cron_schedule` matches the minute of ``now`` and
        it has not already fired in that minute. Each firing goes through `enqueue`,
        so cron triggers dedup and respect the concurrency cap like any other source.
        Returns the jobs enqueued (or deduped onto) this evaluation.
        """
        minute = now.replace(second=0, microsecond=0)
        triggered: list[Job[Fetched]] = []
        for pipeline in await self._cron_pipelines():
            schedule = pipeline.cron_schedule
            if schedule is None:
                continue
            try:
                matched = croniter.match(schedule, minute)
            except (CroniterError, ValueError) as error:
                logger.warning(
                    "skipping invalid cron schedule %r for pipeline %s: %s",
                    schedule,
                    pipeline.id,
                    error,
                )
                continue
            if not matched or self._last_cron_fired.get(pipeline.id) == minute:
                continue
            self._last_cron_fired[pipeline.id] = minute
            triggered.append(await self.enqueue(pipeline.id, TriggerType.CRON))
        return triggered

    async def _cron_loop(self) -> None:
        """Evaluate cron schedules on a fixed interval until cancelled."""
        while True:
            await anyio.sleep(self._cron_interval_seconds)
            try:
                _ = await self.run_cron(self._clock())
            except Exception:
                logger.exception("cron evaluation failed")

    async def _dispatch(self) -> None:
        """Hand pending jobs to the orchestrator up to the concurrency cap.

        Must be called with ``self._lock`` held. Picks the oldest `pending` job not
        already in flight and runs it in the background until either the cap is
        reached or no pending work remains.
        """
        task_group = self._require_task_group()
        while len(self._inflight) < self._max_concurrent_jobs:
            job = await self._next_pending_job()
            if job is None:
                return
            self._inflight.add(job.id)
            _ = task_group.start_soon(self._run, job.id)

    async def _run(self, job_id: str) -> None:
        """Run a dispatched job, then free its slot and dispatch the next pending."""
        try:
            _ = await self._orchestrator.run_job(job_id)
        except Exception:
            logger.exception("scheduled job %s crashed", job_id)
        finally:
            async with self._lock:
                self._inflight.discard(job_id)
                await self._dispatch()

    async def _find_active_job(
        self, pipeline_id: str, trigger_ref: str | None
    ) -> Job[Fetched] | None:
        ref_predicate = (
            Job.trigger_ref.is_null()
            if trigger_ref is None
            else Job.trigger_ref.eq(trigger_ref)
        )
        query = (
            select(Job)
            .where(
                Job.pipeline_id.eq(pipeline_id)
                & ref_predicate
                & Job.status.in_(*_ACTIVE_STATUS_VALUES)
            )
            .order_by(Job.created_at.asc())
            .limit(1)
        )
        async with self._db.transaction() as tx:
            rows = await tx.fetch_all(query)
        return rows[0] if rows else None

    async def _next_pending_job(self) -> Job[Fetched] | None:
        query = select(Job).where(Job.status.eq(JobStatus.PENDING))
        if self._inflight:
            # Exclude jobs already dispatched but not yet advanced past `pending`,
            # so a job is never handed to the orchestrator twice.
            query = query.where(~Job.id.in_(*self._inflight))
        query = query.order_by(Job.created_at.asc()).limit(1)
        async with self._db.transaction() as tx:
            rows = await tx.fetch_all(query)
        return rows[0] if rows else None

    async def _cron_pipelines(self) -> list[Pipeline[Fetched]]:
        query = select(Pipeline).where(~Pipeline.cron_schedule.is_null())
        async with self._db.transaction() as tx:
            return await tx.fetch_all(query)

    def _require_task_group(self) -> TaskGroup:
        if self._task_group is None:
            msg = "Scheduler is not running; enter it as an async context manager"
            raise RuntimeError(msg)
        return self._task_group
