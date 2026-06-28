"""Background supervisor that runs triggered jobs for the HTTP control plane.

`JobManager` wraps a `JobOrchestrator` with an `anyio` task group so a triggered
job runs to completion in the background while the triggering HTTP request returns
immediately. It is an async context manager: entering it starts the task group,
exiting cancels any still in-flight jobs and waits for them to unwind. Cancellation
requests and log-path lookups delegate straight to the orchestrator.

The task group is owned here rather than by FastAPI's lifespan so the control
plane works the same in-process under `httpx.ASGITransport` (which does not run
lifespan events) as it does under uvicorn.
"""

from __future__ import annotations

import logging
from pathlib import Path
from types import TracebackType
from typing import TYPE_CHECKING, Self

import anyio
from anyio.abc import TaskGroup

from danube.domain.enums import TriggerType

if TYPE_CHECKING:
    from snekql.sqlite import Fetched

    from danube.db.models import Job
    from danube.orchestrator.core import JobOrchestrator

logger = logging.getLogger("danube.orchestrator")


class JobManager:
    """Run jobs in the background and expose trigger/cancel/log-path operations."""

    def __init__(self, orchestrator: JobOrchestrator) -> None:
        self._orchestrator = orchestrator
        self._task_group: TaskGroup | None = None

    async def __aenter__(self) -> Self:
        self._task_group = anyio.create_task_group()
        _ = await self._task_group.__aenter__()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool | None:
        task_group = self._require_task_group()
        # Stop supervising: cancel any still-running jobs so shutdown does not block
        # on an in-flight pipeline. The reaper reconciles their runtime state later.
        task_group.cancel_scope.cancel()
        try:
            return await task_group.__aexit__(exc_type, exc, tb)
        finally:
            self._task_group = None

    async def trigger(
        self,
        pipeline_id: str,
        trigger_type: TriggerType = TriggerType.MANUAL,
        trigger_ref: str | None = None,
    ) -> Job[Fetched]:
        """Create a `pending` job and run it in the background; return the row.

        Raises `PipelineNotFoundError` (from the orchestrator) if the pipeline does
        not exist, before any job row is created.
        """
        task_group = self._require_task_group()
        job = await self._orchestrator.create_job(
            pipeline_id, trigger_type, trigger_ref
        )
        _ = task_group.start_soon(self._run, job.id)
        return job

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

    async def _run(self, job_id: str) -> None:
        try:
            _ = await self._orchestrator.run_job(job_id)
        except Exception:
            logger.exception("background job %s crashed", job_id)

    def _require_task_group(self) -> TaskGroup:
        if self._task_group is None:
            msg = "JobManager is not running; enter it as an async context manager"
            raise RuntimeError(msg)
        return self._task_group
