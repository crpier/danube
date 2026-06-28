"""Reaper: retention-based garbage collection for a single-host appliance.

The Reaper is an asyncio interval task that runs daily at 03:00 UTC by default
(`docs/architecture/components.md`, Reaper). Each pass garbage-collects four
classes of data against the `retention.*` policy from `config.json`:

- **Logs** older than `retention.logs_days` (`<data_dir>/logs/<job_id>.log`).
- **Artifacts** older than `retention.artifacts_days`
  (`<data_dir>/artifacts/<job_id>/`).
- **Workspaces** older than `retention.workspaces_days`
  (`<data_dir>/workspaces/<job_id>/`); `workspaces_days == 0` means "delete on
  job end", so any non-active job's workspace is removed regardless of age.
- **Cached images** older than `retention.registry_images_days`, delegated to an
  optional `ImagePruner` (the image cache itself is built elsewhere).

It then reconciles runtime state with the runner and retries cleanup for the
abandoned pods/containers/workspaces and the cleanup attempts that failed during
job teardown. A failed retry is recorded (`danube_runner_cleanup_failures_total`)
and resurfaces on the next pass, because the runner persists a `cleanup_failed`
row that `reconcile()` reports again.

Two invariants protect live data: the Reaper never deletes anything for a job in
an `ACTIVE_STATES` status, and never deletes anything for a job the operator has
flagged for preservation (debugging/retention holds). A global `preserve_all`
flag turns the whole pass into a no-op for retention holds across the appliance.

Filesystem scanning, stat, and deletion are blocking IO, so they run off the
event loop via `anyio.to_thread`. `run_once(now)` takes the clock explicitly so a
pass is deterministic under test; a background task drives it on the interval.
"""

from __future__ import annotations

import logging
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from types import TracebackType
from typing import TYPE_CHECKING, Protocol, Self

import anyio
import anyio.to_thread
from snekql.sqlite import select

from danube.db.models import Job
from danube.domain.lifecycle import ACTIVE_STATES
from danube.domain.runner_types import JobHandle, ReconcileReport
from danube.runner.local import pod_name

if TYPE_CHECKING:
    from anyio.abc import TaskGroup
    from snekql.sqlite import Database

logger = logging.getLogger("danube.reaper")

# Run daily at 03:00 UTC by default (`components.md`, Reaper).
DEFAULT_RUN_AT = time(3, 0, tzinfo=UTC)

# The pod-name prefix the runner stamps on every job pod, derived from the runner
# helper so it cannot drift. Used to map a reconcile report's resource names back
# to job ids.
_POD_PREFIX = pod_name("")
# Resource suffixes the runner appends to container names (`danube-job-<id>-<r>`).
_CONTAINER_SUFFIXES = ("-coordinator", "-worker", "-pod")


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _never_preserved(_job_id: str) -> bool:
    return False


@dataclass(frozen=True, slots=True)
class RetentionConfig:
    """Retention windows (in days) from `config.json`'s `retention` block.

    `workspaces_days == 0` means delete a workspace as soon as its job ends. A
    negative value disables that category. `preserve_all` is a global retention
    hold: when set, a pass performs no deletions or cleanups at all.
    """

    logs_days: int = 30
    artifacts_days: int = 14
    registry_images_days: int = 30
    workspaces_days: int = 0
    preserve_all: bool = False


@dataclass(frozen=True, slots=True)
class ReaperReport:
    """Counts produced by a single `run_once` pass, for logging and metrics."""

    logs_deleted: int = 0
    artifacts_deleted: int = 0
    workspaces_deleted: int = 0
    images_deleted: int = 0
    cleanups_succeeded: int = 0
    cleanups_failed: int = 0
    cleanup_backlog: int = 0


class ImagePruner(Protocol):
    """Prunes cached images created before `older_than`, returning the count.

    Kept narrow and separate from the `Runner` protocol so the Reaper does not
    depend on the image-cache subsystem; a concrete pruner is injected once that
    subsystem exists.
    """

    async def prune_images(self, older_than: datetime) -> int: ...


class ReaperRunner(Protocol):
    """The slice of the `Runner` the Reaper needs: reconcile and retry cleanup."""

    async def reconcile(self) -> ReconcileReport: ...
    async def cleanup_job(self, job: JobHandle) -> None: ...


@dataclass(frozen=True, slots=True)
class ReaperConfig:
    """Everything tunable about a `Reaper`, bundled so the constructor stays small.

    `image_pruner` is the optional cached-image GC hook (absent until the image
    cache exists), `run_at` the daily UTC time the background loop fires, and
    `is_preserved` an operator hook to hold a specific job's data (debug/retention
    flags). The defaults are the secure single-host appliance settings.
    """

    retention: RetentionConfig = field(default_factory=RetentionConfig)
    image_pruner: ImagePruner | None = None
    run_at: time = DEFAULT_RUN_AT
    is_preserved: Callable[[str], bool] = _never_preserved


_DEFAULT_CONFIG = ReaperConfig()


@dataclass(frozen=True, slots=True)
class _CleanupOutcome:
    succeeded: int
    failed: int
    backlog: int


class Reaper:
    """Interval-driven retention GC with an active-job and preservation guard."""

    def __init__(
        self,
        db: Database,
        runner: ReaperRunner,
        data_dir: Path | str,
        *,
        config: ReaperConfig = _DEFAULT_CONFIG,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._db = db
        self._runner = runner
        self._data_dir = Path(data_dir)
        self._retention = config.retention
        self._image_pruner = config.image_pruner
        self._run_at = config.run_at
        self._is_preserved = config.is_preserved
        self._clock = clock
        self._task_group: TaskGroup | None = None
        # Cumulative cleanup failures (`danube_runner_cleanup_failures_total`) and
        # the backlog left by the most recent pass, both read by health checks.
        self._cleanup_failures_total = 0
        self._cleanup_backlog = 0
        self._last_run: datetime | None = None

    async def __aenter__(self) -> Self:
        self._task_group = anyio.create_task_group()
        _ = await self._task_group.__aenter__()
        _ = self._task_group.start_soon(self._loop)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool | None:
        if self._task_group is None:
            return None
        self._task_group.cancel_scope.cancel()
        try:
            return await self._task_group.__aexit__(exc_type, exc, tb)
        finally:
            self._task_group = None

    # --- metrics / health signals ------------------------------------------

    @property
    def cleanup_failures_total(self) -> int:
        """Cumulative `runner.cleanup_job` failures across all passes."""
        return self._cleanup_failures_total

    @property
    def cleanup_backlog(self) -> int:
        """Jobs still awaiting cleanup after the most recent pass (retried next)."""
        return self._cleanup_backlog

    @property
    def last_run(self) -> datetime | None:
        return self._last_run

    # --- the interval loop --------------------------------------------------

    async def _loop(self) -> None:
        """Sleep until the next scheduled time, run a pass, repeat until cancelled."""
        while True:
            await anyio.sleep(self.seconds_until_next_run(self._clock()))
            try:
                _ = await self.run_once(self._clock())
            except Exception:
                logger.exception("reaper pass failed")

    def seconds_until_next_run(self, now: datetime) -> float:
        """Seconds from `now` until the next occurrence of the configured time."""
        target = datetime.combine(now.date(), self._run_at)
        if target <= now:
            target += timedelta(days=1)
        return (target - now).total_seconds()

    # --- a single pass ------------------------------------------------------

    async def run_once(self, now: datetime) -> ReaperReport:
        """Garbage-collect everything due as of `now` and return the counts."""
        self._last_run = now
        if self._retention.preserve_all:
            logger.info("reaper pass skipped: global retention hold (preserve_all)")
            self._cleanup_backlog = 0
            return ReaperReport()

        active = await self._active_job_ids()
        logs, artifacts, workspaces = await anyio.to_thread.run_sync(
            self._sweep_filesystem, now, active
        )
        images = await self._reap_images(now)
        cleanup = await self._reconcile_and_cleanup(active)

        self._cleanup_failures_total += cleanup.failed
        self._cleanup_backlog = cleanup.backlog
        return ReaperReport(
            logs_deleted=logs,
            artifacts_deleted=artifacts,
            workspaces_deleted=workspaces,
            images_deleted=images,
            cleanups_succeeded=cleanup.succeeded,
            cleanups_failed=cleanup.failed,
            cleanup_backlog=cleanup.backlog,
        )

    async def _reap_images(self, now: datetime) -> int:
        days = self._retention.registry_images_days
        if self._image_pruner is None or days <= 0:
            return 0
        cutoff = now - timedelta(days=days)
        return await self._image_pruner.prune_images(cutoff)

    async def _reconcile_and_cleanup(self, active: frozenset[str]) -> _CleanupOutcome:
        report = await self._runner.reconcile()
        targets = _cleanup_targets(report) - active
        succeeded = 0
        failed = 0
        for job_id in sorted(targets):
            if self._is_preserved(job_id):
                continue
            handle = JobHandle(
                job_id=job_id,
                pod_id=pod_name(job_id),
                workspace_path=str(self._workspace_path(job_id)),
            )
            try:
                await self._runner.cleanup_job(handle)
            except Exception:
                logger.exception("reaper cleanup failed for job %s", job_id)
                failed += 1
            else:
                succeeded += 1
        return _CleanupOutcome(succeeded=succeeded, failed=failed, backlog=failed)

    # --- filesystem sweep (runs in a worker thread) ------------------------

    def _sweep_filesystem(
        self, now: datetime, active: frozenset[str]
    ) -> tuple[int, int, int]:
        logs = self._sweep_logs(now, active)
        artifacts = self._sweep_dirs(
            self._data_dir / "artifacts",
            self._cutoff(now, self._retention.artifacts_days),
            active,
        )
        workspaces = self._sweep_dirs(
            self._data_dir / "workspaces",
            self._workspaces_cutoff(now),
            active,
        )
        return logs, artifacts, workspaces

    def _sweep_logs(self, now: datetime, active: frozenset[str]) -> int:
        cutoff = self._cutoff(now, self._retention.logs_days)
        if cutoff is _DISABLED:
            return 0
        root = self._data_dir / "logs"
        if not root.is_dir():
            return 0
        deleted = 0
        for path in root.iterdir():
            if path.suffix != ".log" or not path.is_file():
                continue
            if self._kept(path.stem, path, cutoff, active):
                continue
            path.unlink(missing_ok=True)
            deleted += 1
        return deleted

    def _sweep_dirs(
        self, root: Path, cutoff: datetime | None | object, active: frozenset[str]
    ) -> int:
        if cutoff is _DISABLED:
            return 0
        if not root.is_dir():
            return 0
        deleted = 0
        for path in root.iterdir():
            if not path.is_dir():
                continue
            if self._kept(path.name, path, cutoff, active):
                continue
            # `ignore_errors` keeps a single undeletable entry from aborting the
            # sweep; reconcile/retry covers anything genuinely stuck.
            _rmtree(path)
            deleted += 1
        return deleted

    def _kept(
        self,
        job_id: str,
        path: Path,
        cutoff: datetime | None | object,
        active: frozenset[str],
    ) -> bool:
        """Whether `path` must be preserved this pass (never deleted)."""
        if job_id in active or self._is_preserved(job_id):
            return True
        # `cutoff is None` means "delete regardless of age" (workspaces_days == 0).
        if cutoff is None:
            return False
        assert isinstance(cutoff, datetime)
        return _mtime(path) >= cutoff

    def _cutoff(self, now: datetime, days: int) -> datetime | object:
        """A datetime threshold for `days`, or `_DISABLED` for `days <= 0`."""
        if days <= 0:
            return _DISABLED
        return now - timedelta(days=days)

    def _workspaces_cutoff(self, now: datetime) -> datetime | None | object:
        days = self._retention.workspaces_days
        if days == 0:
            # "Delete on job end": any non-active workspace, regardless of age.
            return None
        if days < 0:
            return _DISABLED
        return now - timedelta(days=days)

    def _workspace_path(self, job_id: str) -> Path:
        return self._data_dir / "workspaces" / job_id

    async def _active_job_ids(self) -> frozenset[str]:
        statuses = tuple(ACTIVE_STATES)
        query = select(Job.id).where(Job.status.in_(*statuses))
        async with self._db.transaction() as tx:
            rows = await tx.fetch_all(query)
        return frozenset(rows)


# Sentinel distinguishing "category disabled" from a real cutoff or the
# delete-everything `None`; an object identity, never equal to a datetime.
_DISABLED = object()


def _cleanup_targets(report: ReconcileReport) -> frozenset[str]:
    """Job ids to retry cleanup for, derived from a reconcile report.

    `missing_pods` is excluded: it flags an *active* job whose pod vanished, which
    is a status-reconciliation concern, not an abandoned resource to delete.
    """
    targets: set[str] = set()
    for name in report.stale_pods:
        targets.add(_job_id_from_resource(name))
    for name in report.orphaned_containers:
        targets.add(_job_id_from_resource(name))
    targets.update(report.stale_workspaces)
    targets.update(report.failed_cleanups)
    return frozenset(targets)


def _job_id_from_resource(name: str) -> str:
    """Recover a job id from a pod or container resource name."""
    base = name.removeprefix(_POD_PREFIX)
    for suffix in _CONTAINER_SUFFIXES:
        if base.endswith(suffix):
            return base[: -len(suffix)]
    return base


def _mtime(path: Path) -> datetime:
    return datetime.fromtimestamp(path.stat().st_mtime, UTC)


def _rmtree(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)
