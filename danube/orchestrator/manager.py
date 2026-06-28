"""Background supervisor that runs triggered jobs for the HTTP control plane.

`JobManager` is the control plane's façade over the `Scheduler`. Triggering a run
goes through the scheduler's single enqueue path, so a manual HTTP trigger gets the
same deduplication and global concurrency cap as cron- and webhook-sourced runs.
The scheduler owns the `anyio` task group that runs jobs in the background (and the
cron evaluation loop), so the triggering HTTP request returns immediately while the
job runs to completion.

`JobManager` is an async context manager that simply enters/exits the scheduler.
Cancellation requests and job/log-path lookups delegate straight to the
orchestrator. The task group is owned by the scheduler rather than by FastAPI's
lifespan so the control plane works the same in-process under `httpx.ASGITransport`
(which does not run lifespan events) as it does under uvicorn.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Self

from danube.domain.enums import TriggerType
from danube.orchestrator.scheduler import DEFAULT_MAX_CONCURRENT_JOBS, Scheduler

if TYPE_CHECKING:
    from pathlib import Path
    from types import TracebackType

    from snekql.sqlite import Fetched

    from danube.db.models import Job
    from danube.orchestrator.core import JobOrchestrator

logger = logging.getLogger("danube.orchestrator")


class JobManager:
    """Run jobs in the background and expose trigger/cancel/log-path operations."""

    def __init__(
        self,
        orchestrator: JobOrchestrator,
        *,
        max_concurrent_jobs: int = DEFAULT_MAX_CONCURRENT_JOBS,
    ) -> None:
        self._orchestrator = orchestrator
        self._scheduler = Scheduler(
            orchestrator, orchestrator.db, max_concurrent_jobs=max_concurrent_jobs
        )

    async def __aenter__(self) -> Self:
        _ = await self._scheduler.__aenter__()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool | None:
        return await self._scheduler.__aexit__(exc_type, exc, tb)

    async def trigger(
        self,
        pipeline_id: str,
        trigger_type: TriggerType = TriggerType.MANUAL,
        trigger_ref: str | None = None,
    ) -> Job[Fetched]:
        """Enqueue a run through the scheduler and return the job row.

        Goes through the shared enqueue path, so the trigger deduplicates against
        any active job for the same pipeline/ref and respects the global concurrency
        cap before being handed to the orchestrator. Raises `PipelineNotFoundError`
        (from the orchestrator) if the pipeline does not exist, before any job row
        is created.
        """
        return await self._scheduler.enqueue(pipeline_id, trigger_type, trigger_ref)

    async def cancel(self, job_id: str) -> Job[Fetched]:
        """Request cancellation of a running job and return its current row.

        The job transitions to `cancelled` in its background task; the returned row
        is a snapshot taken right after the request and may still read `running`.
        """
        await self._orchestrator.request_cancel(job_id)
        return await self._orchestrator.fetch_job(job_id)

    async def fetch_job(self, job_id: str) -> Job[Fetched]:
        """Return a job row, raising `JobNotFoundError` if it does not exist."""
        return await self._orchestrator.fetch_job(job_id)

    def log_path(self, job_id: str) -> Path:
        """Return the on-disk path of a job's append-only log file."""
        return self._orchestrator.log_path(job_id)
