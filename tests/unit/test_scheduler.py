"""Tests for the `Scheduler`: the single trigger/enqueue path.

The scheduler owns deduplication (one active job per pipeline/ref), a global
concurrency cap (excess triggers stay `pending`), and cron evaluation against a
caller-supplied clock (`run_cron(now)`). These tests drive it with `FakeRunner`
(jobs either complete immediately or hang forever), a real `JobOrchestrator`, an
in-memory database, and a temp data directory. No Podman, no HTTP.
"""

import shutil
import tempfile
from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import anyio
from snekql.sqlite import Database, Fetched, insert, select
from snektest import assert_eq, assert_raises, fixture, load_fixture, test

from danube.db import open_database
from danube.db.models import Job, Pipeline, Team
from danube.domain.enums import JobStatus, TriggerType
from danube.domain.lifecycle import TERMINAL_STATES
from danube.orchestrator import JobOrchestrator, PipelineNotFoundError, Scheduler
from danube.rpc import ControlPlane
from danube.runner import FakeRunner

RPC_ADDRESS = "http://master.test:9000"
T0 = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)


async def _sleep_forever(_env: Mapping[str, str]) -> int:
    await anyio.sleep_forever()
    raise AssertionError


@dataclass
class Env:
    db: Database
    scheduler: Scheduler
    orchestrator: JobOrchestrator


async def _seed_pipeline(
    db: Database,
    pipeline_id: str,
    *,
    cron_schedule: str | None = None,
) -> None:
    async with db.transaction() as tx:
        await tx.execute(
            insert(
                Pipeline(
                    id=pipeline_id,
                    name="demo",
                    team_id="t1",
                    repo_url="https://example.test/repo.git",
                    worker_image="img:latest",
                    cron_schedule=cron_schedule,
                )
            )
        )


@asynccontextmanager
async def _make_env(
    runner: FakeRunner, *, max_concurrent_jobs: int = 4
) -> AsyncGenerator[Env]:
    data_dir = Path(tempfile.mkdtemp(prefix="danube-sched-"))
    db = await open_database(":memory:")
    try:
        async with db.transaction() as tx:
            await tx.execute(insert(Team(id="t1", name="team", global_admin=False)))
        control_plane = ControlPlane(runner, db, data_dir)
        orchestrator = JobOrchestrator(
            runner, db, data_dir, control_plane, rpc_address=RPC_ADDRESS
        )
        # A long cron interval keeps the background loop from firing during tests;
        # cron behaviour is driven explicitly through `run_cron(now)`.
        async with Scheduler(
            orchestrator,
            db,
            max_concurrent_jobs=max_concurrent_jobs,
            cron_interval_seconds=3600.0,
        ) as scheduler:
            yield Env(db, scheduler, orchestrator)
    finally:
        await db.close()
        shutil.rmtree(data_dir, ignore_errors=True)


@fixture
async def env() -> AsyncGenerator[Env]:
    async with _make_env(FakeRunner()) as resources:
        yield resources


async def _all_jobs(db: Database) -> list[Job[Fetched]]:
    async with db.transaction() as tx:
        return await tx.fetch_all(select(Job).all())


async def _status_counts(db: Database) -> dict[str, int]:
    counts: dict[str, int] = {}
    for job in await _all_jobs(db):
        counts[job.status] = counts.get(job.status, 0) + 1
    return counts


async def _wait_until(
    predicate: Callable[[], Awaitable[bool]], timeout_seconds: float = 5.0
) -> None:
    with anyio.fail_after(timeout_seconds):
        while True:
            if await predicate():
                return
            await anyio.sleep(0.01)


async def _job_status(db: Database, job_id: str) -> str:
    async with db.transaction() as tx:
        job = await tx.fetch_one(select(Job).where(Job.id.eq(job_id)))
    return job.status


@test(mark="medium")
async def test_enqueue_creates_pending_job_with_trigger_type() -> None:
    environment = await load_fixture(env())
    await _seed_pipeline(environment.db, "p1")

    job = await environment.scheduler.enqueue("p1", TriggerType.MANUAL, "main")

    assert_eq(job.pipeline_id, "p1")
    assert_eq(job.trigger_type, TriggerType.MANUAL)
    assert_eq(job.trigger_ref, "main")


@test(mark="medium")
async def test_enqueue_unknown_pipeline_raises() -> None:
    environment = await load_fixture(env())

    with assert_raises(PipelineNotFoundError):
        _ = await environment.scheduler.enqueue("nope", TriggerType.MANUAL)


@test(mark="medium")
async def test_duplicate_triggers_dedup_to_one_active_job() -> None:
    async with _make_env(FakeRunner(coordinator=_sleep_forever)) as environment:
        await _seed_pipeline(environment.db, "p1")

        first = await environment.scheduler.enqueue("p1", TriggerType.MANUAL, "main")
        second = await environment.scheduler.enqueue("p1", TriggerType.MANUAL, "main")
        other_ref = await environment.scheduler.enqueue(
            "p1", TriggerType.MANUAL, "release"
        )

        # Same pipeline/ref collapses onto the existing active job; a different
        # ref is a distinct job.
        assert_eq(second.id, first.id)
        assert other_ref.id != first.id
        assert_eq(len(await _all_jobs(environment.db)), 2)


@test(mark="medium")
async def test_manual_trigger_reuses_dedup_path() -> None:
    """A manual trigger dedups exactly like any other source."""
    async with _make_env(FakeRunner(coordinator=_sleep_forever)) as environment:
        await _seed_pipeline(environment.db, "p1")

        manual = await environment.scheduler.enqueue("p1", TriggerType.MANUAL, "main")
        cron_same_ref = await environment.scheduler.enqueue(
            "p1", TriggerType.CRON, "main"
        )

        assert_eq(cron_same_ref.id, manual.id)


@test(mark="medium")
async def test_global_concurrency_cap_keeps_excess_pending() -> None:
    async with _make_env(
        FakeRunner(coordinator=_sleep_forever), max_concurrent_jobs=1
    ) as environment:
        await _seed_pipeline(environment.db, "p1")

        running = await environment.scheduler.enqueue("p1", TriggerType.MANUAL, "r1")
        _ = await environment.scheduler.enqueue("p1", TriggerType.MANUAL, "r2")
        _ = await environment.scheduler.enqueue("p1", TriggerType.MANUAL, "r3")

        # The first job is handed to the orchestrator and reaches `running`; the
        # other two exceed the cap and stay `pending`.
        await _wait_until(lambda: _running(environment.db, running.id))
        counts = await _status_counts(environment.db)
        assert_eq(counts.get(JobStatus.PENDING, 0), 2)
        assert_eq(counts.get(JobStatus.RUNNING, 0), 1)


@test(mark="medium")
async def test_freed_capacity_dispatches_a_pending_job() -> None:
    async with _make_env(
        FakeRunner(coordinator=_sleep_forever), max_concurrent_jobs=1
    ) as environment:
        await _seed_pipeline(environment.db, "p1")
        first = await environment.scheduler.enqueue("p1", TriggerType.MANUAL, "r1")
        _ = await environment.scheduler.enqueue("p1", TriggerType.MANUAL, "r2")
        await _wait_until(lambda: _running(environment.db, first.id))

        # Cancel the running job; its freed slot must dispatch a pending one.
        await environment.orchestrator.request_cancel(first.id)

        await _wait_until(lambda: _has_running(environment.db, exclude=first.id))
        counts = await _status_counts(environment.db)
        assert_eq(counts.get(JobStatus.RUNNING, 0), 1)
        assert_eq(counts.get(JobStatus.PENDING, 0), 0)


@test(mark="medium")
async def test_cron_enqueues_one_job_per_matching_minute() -> None:
    environment = await load_fixture(env())
    await _seed_pipeline(environment.db, "p_cron", cron_schedule="* * * * *")

    fired = await environment.scheduler.run_cron(T0)
    assert_eq(len(fired), 1)
    # A second evaluation in the same minute must not enqueue a duplicate.
    again = await environment.scheduler.run_cron(T0)
    assert_eq(len(again), 0)
    assert_eq(len(await _all_jobs(environment.db)), 1)
    assert_eq((await _all_jobs(environment.db))[0].trigger_type, TriggerType.CRON)

    # Let the first run finish so it no longer dedups the next minute's trigger.
    await _wait_until(lambda: _no_active(environment.db))
    next_minute = await environment.scheduler.run_cron(T0 + timedelta(minutes=1))
    assert_eq(len(next_minute), 1)
    assert_eq(len(await _all_jobs(environment.db)), 2)


@test(mark="medium")
async def test_cron_does_not_fire_on_a_non_matching_minute() -> None:
    environment = await load_fixture(env())
    await _seed_pipeline(environment.db, "p_cron", cron_schedule="0 0 * * *")

    fired = await environment.scheduler.run_cron(
        datetime(2026, 1, 1, 12, 30, 0, tzinfo=UTC)
    )

    assert_eq(fired, [])
    assert_eq(await _all_jobs(environment.db), [])


async def _running(db: Database, job_id: str) -> bool:
    return await _job_status(db, job_id) == JobStatus.RUNNING


async def _has_running(db: Database, *, exclude: str) -> bool:
    for job in await _all_jobs(db):
        if job.id != exclude and job.status == JobStatus.RUNNING:
            return True
    return False


async def _no_active(db: Database) -> bool:
    return all(JobStatus(job.status) in TERMINAL_STATES for job in await _all_jobs(db))
