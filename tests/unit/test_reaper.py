"""Unit tests for the `Reaper` retention GC task.

The reaper is driven with a controllable clock and a temp data dir so the
retention windows are deterministic: files stamped past their window are deleted,
files inside it are kept, and data for an active or preservation-flagged job is
never touched. Reconcile-driven cleanup is exercised with a stub runner that can
fail a cleanup once and succeed on the retry, mirroring a cleanup that failed
during job teardown and is reattempted on the next pass.
"""

import logging
import os
import shutil
import tempfile
from collections.abc import AsyncGenerator, Generator
from datetime import UTC, datetime, time, timedelta
from pathlib import Path

from snekql.sqlite import Database, insert
from snektest import assert_eq, fixture, load_fixture, test

from danube.db import open_database
from danube.db.models import Job, Pipeline, Team
from danube.domain.enums import JobStatus, TriggerType
from danube.domain.runner_types import JobHandle, ReconcileReport
from danube.orchestrator.reaper import (
    Reaper,
    ReaperConfig,
    ReaperRunner,
    RetentionConfig,
)
from danube.runner.local import pod_name
from danube.runner.podman import PodmanError

NOW = datetime(2026, 6, 28, 3, 0, tzinfo=UTC)


@fixture
def data_dir() -> Generator[Path]:
    path = Path(tempfile.mkdtemp(prefix="danube-reaper-"))
    yield path
    shutil.rmtree(path, ignore_errors=True)


@fixture
async def memory_db() -> AsyncGenerator[Database]:
    db = await open_database(":memory:")
    try:
        yield db
    finally:
        await db.close()


async def _seed_job(db: Database, job_id: str, status: JobStatus) -> None:
    """Seed the shared team/pipeline and one job. Called once per test database."""
    async with db.transaction() as tx:
        await tx.execute(insert(Team(id="t1", name="team", global_admin=False)))
        await tx.execute(
            insert(
                Pipeline(
                    id="p1",
                    name="demo",
                    team_id="t1",
                    repo_url="https://example.test/repo.git",
                    worker_image="busybox:latest",
                )
            )
        )
        await tx.execute(
            insert(
                Job(
                    id=job_id,
                    pipeline_id="p1",
                    trigger_type=TriggerType.MANUAL,
                    status=status,
                )
            )
        )


def _write_log(data: Path, job_id: str, *, age_days: float) -> Path:
    logs = data / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    path = logs / f"{job_id}.log"
    _ = path.write_text("log contents\n")
    _stamp(path, age_days)
    return path


def _make_dir(data: Path, kind: str, job_id: str, *, age_days: float) -> Path:
    root = data / kind / job_id
    root.mkdir(parents=True, exist_ok=True)
    _ = (root / "file").write_text("data\n")
    _stamp(root, age_days)
    return root


def _stamp(path: Path, age_days: float) -> None:
    ts = (NOW - timedelta(days=age_days)).timestamp()
    os.utime(path, (ts, ts))


def _exists(path: Path) -> bool:
    return path.exists()


class _StubRunner:
    """Reconcile/cleanup stub: returns a fixed report and records cleanups.

    Job ids listed in `fail_jobs` raise `PodmanError` from `cleanup_job`, modeling
    a cleanup that failed during teardown; clearing the set between passes models a
    later retry succeeding.
    """

    def __init__(
        self, report: ReconcileReport, *, fail_jobs: set[str] | None = None
    ) -> None:
        self.report = report
        self.fail_jobs = fail_jobs or set()
        self.cleaned: list[str] = []
        self.reconcile_calls = 0

    async def reconcile(self) -> ReconcileReport:
        self.reconcile_calls += 1
        return self.report

    async def cleanup_job(self, job: JobHandle) -> None:
        self.cleaned.append(job.job_id)
        if job.job_id in self.fail_jobs:
            error = PodmanError("DELETE", f"/pods/{job.pod_id}", 500, "boom")
            raise error


class _RecordingPruner:
    def __init__(self) -> None:
        self.cutoffs: list[datetime] = []

    async def prune_images(self, older_than: datetime) -> int:
        self.cutoffs.append(older_than)
        return 2


# Typed references asserting the stubs satisfy the protocols the reaper depends on.
_RUNNER: ReaperRunner = _StubRunner(ReconcileReport())


def _config(
    *,
    retention: RetentionConfig | None = None,
    preserved: set[str] | None = None,
    pruner: _RecordingPruner | None = None,
) -> ReaperConfig:
    held = preserved or set()
    return ReaperConfig(
        retention=retention or RetentionConfig(),
        image_pruner=pruner,
        is_preserved=lambda job_id: job_id in held,
    )


def _make_reaper(
    db: Database,
    data: Path,
    *,
    runner: ReaperRunner | None = None,
    config: ReaperConfig | None = None,
) -> Reaper:
    return Reaper(
        db,
        runner or _StubRunner(ReconcileReport()),
        data,
        config=config or ReaperConfig(),
    )


@test(mark="medium")
async def test_logs_past_window_deleted_recent_kept() -> None:
    db = await load_fixture(memory_db())
    data = load_fixture(data_dir())
    old = _write_log(data, "old", age_days=40)
    fresh = _write_log(data, "fresh", age_days=5)
    reaper = _make_reaper(
        db, data, config=_config(retention=RetentionConfig(logs_days=30))
    )

    report = await reaper.run_once(NOW)

    assert not _exists(old)
    assert _exists(fresh)
    assert_eq(report.logs_deleted, 1)


@test(mark="medium")
async def test_artifacts_and_workspaces_respect_windows() -> None:
    db = await load_fixture(memory_db())
    data = load_fixture(data_dir())
    old_art = _make_dir(data, "artifacts", "a-old", age_days=30)
    fresh_art = _make_dir(data, "artifacts", "a-new", age_days=2)
    old_ws = _make_dir(data, "workspaces", "w-old", age_days=10)
    fresh_ws = _make_dir(data, "workspaces", "w-new", age_days=1)
    reaper = _make_reaper(
        db,
        data,
        config=_config(retention=RetentionConfig(artifacts_days=14, workspaces_days=7)),
    )

    report = await reaper.run_once(NOW)

    assert not _exists(old_art)
    assert _exists(fresh_art)
    assert not _exists(old_ws)
    assert _exists(fresh_ws)
    assert_eq(report.artifacts_deleted, 1)
    assert_eq(report.workspaces_deleted, 1)


@test(mark="medium")
async def test_workspaces_days_zero_deletes_on_job_end() -> None:
    db = await load_fixture(memory_db())
    data = load_fixture(data_dir())
    # workspaces_days=0 means "delete on job end": any terminal job's workspace is
    # removed regardless of age, but an active job's workspace is preserved.
    await _seed_job(db, "active", JobStatus.RUNNING)
    terminal = _make_dir(data, "workspaces", "terminal", age_days=0)
    active = _make_dir(data, "workspaces", "active", age_days=0)
    reaper = _make_reaper(
        db, data, config=_config(retention=RetentionConfig(workspaces_days=0))
    )

    report = await reaper.run_once(NOW)

    assert not _exists(terminal)
    assert _exists(active)
    assert_eq(report.workspaces_deleted, 1)


@test(mark="medium")
async def test_active_job_data_never_deleted() -> None:
    db = await load_fixture(memory_db())
    data = load_fixture(data_dir())
    await _seed_job(db, "live", JobStatus.RUNNING)
    log = _write_log(data, "live", age_days=99)
    art = _make_dir(data, "artifacts", "live", age_days=99)
    reaper = _make_reaper(
        db,
        data,
        config=_config(retention=RetentionConfig(logs_days=1, artifacts_days=1)),
    )

    report = await reaper.run_once(NOW)

    assert _exists(log)
    assert _exists(art)
    assert_eq(report.logs_deleted, 0)
    assert_eq(report.artifacts_deleted, 0)


@test(mark="medium")
async def test_preserved_job_data_kept() -> None:
    db = await load_fixture(memory_db())
    data = load_fixture(data_dir())
    log = _write_log(data, "keep", age_days=99)
    reaper = _make_reaper(
        db,
        data,
        config=_config(retention=RetentionConfig(logs_days=1), preserved={"keep"}),
    )

    report = await reaper.run_once(NOW)

    assert _exists(log)
    assert_eq(report.logs_deleted, 0)


@test(mark="medium")
async def test_preserve_all_holds_everything() -> None:
    db = await load_fixture(memory_db())
    data = load_fixture(data_dir())
    log = _write_log(data, "old", age_days=99)
    runner = _StubRunner(ReconcileReport(stale_pods=[pod_name("stale")]))
    reaper = _make_reaper(
        db,
        data,
        runner=runner,
        config=_config(retention=RetentionConfig(preserve_all=True)),
    )

    report = await reaper.run_once(NOW)

    assert _exists(log)
    assert_eq(report.logs_deleted, 0)
    assert_eq(runner.cleaned, [])


@test(mark="medium")
async def test_reconcile_cleans_stale_resource() -> None:
    db = await load_fixture(memory_db())
    data = load_fixture(data_dir())
    runner = _StubRunner(
        ReconcileReport(
            stale_pods=[pod_name("stale")],
            orphaned_containers=[f"{pod_name('orphan')}-worker"],
            stale_workspaces=["lonely-ws"],
            failed_cleanups=["prev-fail"],
            missing_pods=["active-no-pod"],
        )
    )
    reaper = _make_reaper(db, data, runner=runner)

    report = await reaper.run_once(NOW)

    # Stale pods/containers/workspaces and prior failed cleanups are retried;
    # `missing_pods` (an active job whose pod vanished) is not a cleanup target.
    assert_eq(sorted(runner.cleaned), ["lonely-ws", "orphan", "prev-fail", "stale"])
    assert_eq(report.cleanups_succeeded, 4)
    assert_eq(report.cleanups_failed, 0)


@test(mark="medium")
async def test_cleanup_failure_recorded_and_retried() -> None:
    db = await load_fixture(memory_db())
    data = load_fixture(data_dir())
    runner = _StubRunner(
        ReconcileReport(stale_pods=[pod_name("flaky")]), fail_jobs={"flaky"}
    )
    reaper = _make_reaper(db, data, runner=runner)

    first = await reaper.run_once(NOW)

    assert_eq(first.cleanups_failed, 1)
    assert_eq(first.cleanup_backlog, 1)
    assert_eq(reaper.cleanup_failures_total, 1)
    assert_eq(reaper.cleanup_backlog, 1)

    # Next pass: the underlying cause cleared, so the retry succeeds and the
    # backlog drains while the cumulative failure counter is retained.
    runner.fail_jobs.clear()
    second = await reaper.run_once(NOW + timedelta(days=1))

    assert_eq(second.cleanups_succeeded, 1)
    assert_eq(second.cleanup_backlog, 0)
    assert_eq(reaper.cleanup_failures_total, 1)
    assert_eq(reaper.cleanup_backlog, 0)


@test(mark="medium")
async def test_image_pruner_called_with_cutoff() -> None:
    db = await load_fixture(memory_db())
    data = load_fixture(data_dir())
    pruner = _RecordingPruner()
    reaper = _make_reaper(
        db,
        data,
        config=_config(
            retention=RetentionConfig(registry_images_days=30), pruner=pruner
        ),
    )

    report = await reaper.run_once(NOW)

    assert_eq(pruner.cutoffs, [NOW - timedelta(days=30)])
    assert_eq(report.images_deleted, 2)


@test(mark="medium")
async def test_pass_emits_cleanup_metric_signals() -> None:
    db = await load_fixture(memory_db())
    data = load_fixture(data_dir())
    runner = _StubRunner(
        ReconcileReport(stale_pods=[pod_name("flaky")]), fail_jobs={"flaky"}
    )
    reaper = _make_reaper(db, data, runner=runner)
    records: list[logging.LogRecord] = []
    handler = logging.Handler()
    handler.emit = records.append  # type: ignore[method-assign]
    logger = logging.getLogger("danube.reaper")
    logger.addHandler(handler)
    previous_level = logger.level
    logger.setLevel(logging.INFO)
    try:
        _ = await reaper.run_once(NOW)
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)

    # The cumulative-failure counter and current backlog are emitted as a
    # structured log `extra` so a metrics exporter can scrape them, matching the
    # `*_total` convention the blueprint syncer uses.
    emitted = [
        record.__dict__
        for record in records
        if "danube_runner_cleanup_failures_total" in record.__dict__
    ]
    assert_eq(len(emitted), 1)
    assert_eq(emitted[0]["danube_runner_cleanup_failures_total"], 1)
    assert_eq(emitted[0]["cleanup_backlog"], 1)


@test(mark="fast")
def test_seconds_until_next_run_targets_configured_time() -> None:
    reaper = Reaper.__new__(Reaper)
    reaper._run_at = time(3, 0, tzinfo=UTC)

    before = datetime(2026, 6, 28, 1, 0, tzinfo=UTC)
    after = datetime(2026, 6, 28, 4, 0, tzinfo=UTC)

    assert_eq(reaper.seconds_until_next_run(before), 2 * 3600)
    assert_eq(reaper.seconds_until_next_run(after), 23 * 3600)
