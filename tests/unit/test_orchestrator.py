"""Tests for the coordinator-driven `JobOrchestrator` and `LogWriter`.

The orchestrator no longer runs steps itself: it asks the runner to create the
environment, opens a control-plane session, starts the Coordinator, and waits for
it to exit. These tests drive that lifecycle with `FakeRunner` (whose Coordinator
is simulated by an exit code or a hanging program) plus a real `ControlPlane`, an
in-memory database, and a temp data directory. No Podman, no HTTP. The full
RPC->exec->log->exit-code loop is covered in `tests/integration/test_job_run.py`.
"""

import shutil
import tempfile
from collections.abc import AsyncGenerator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import anyio
from snekql.sqlite import Database, Fetched, insert, select, update
from snektest import assert_eq, assert_raises, fixture, load_fixture, test

from danube.db import open_database
from danube.db.models import Job, Pipeline, Team
from danube.domain.enums import JobStatus, TriggerType
from danube.domain.lifecycle import InvalidTransition
from danube.domain.limits import DEFAULT_CEILING, ResourceCeiling, ResourceRequest
from danube.domain.runner_types import JobHandle, StartJobRequest
from danube.orchestrator import JobOrchestrator, LogWriter, LogWriterError
from danube.rpc import ControlPlane
from danube.runner import FakeRunner, RecordedCall
from danube.security import SecretCipher, SecretService, generate_key

RPC_ADDRESS = "http://master.test:9000"


class _StartFailedError(Exception):
    """Simulated runner failure: the environment could not be created."""


class _FailingStartRunner(FakeRunner):
    """A runner whose `start_job` always fails before any environment exists."""

    async def start_job(self, request: StartJobRequest) -> JobHandle:
        self.calls.append(RecordedCall("start_job", (request,)))
        raise _StartFailedError


async def _sleep_forever(_env: Mapping[str, str]) -> int:
    await anyio.sleep_forever()
    raise AssertionError


@dataclass
class Env:
    db: Database
    orchestrator: JobOrchestrator
    runner: FakeRunner
    data_dir: Path


async def _seed_pipeline(
    db: Database, pipeline_id: str, max_duration_seconds: int = 3600
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
                    max_duration_seconds=max_duration_seconds,
                )
            )
        )


@asynccontextmanager
async def _make_env(
    runner: FakeRunner, *, limits: ResourceCeiling = DEFAULT_CEILING
) -> AsyncGenerator[Env]:
    data_dir = Path(tempfile.mkdtemp(prefix="danube-orch-"))
    db = await open_database(":memory:")
    try:
        async with db.transaction() as tx:
            await tx.execute(insert(Team(id="t1", name="team", global_admin=False)))
        await _seed_pipeline(db, "p1")
        control_plane = ControlPlane(
            runner,
            db,
            data_dir,
            secret_service=SecretService(db, SecretCipher(generate_key())),
        )
        orchestrator = JobOrchestrator(
            runner, db, data_dir, control_plane, rpc_address=RPC_ADDRESS, limits=limits
        )
        yield Env(db, orchestrator, runner, data_dir)
    finally:
        await db.close()
        shutil.rmtree(data_dir, ignore_errors=True)


@fixture
async def env() -> AsyncGenerator[Env]:
    async with _make_env(FakeRunner()) as resources:
        yield resources


async def _job(db: Database, job_id: str) -> Job[Fetched]:
    async with db.transaction() as tx:
        return await tx.fetch_one(select(Job).where(Job.id.eq(job_id)))


@test(mark="medium")
async def test_create_job_persists_pending() -> None:
    e = await load_fixture(env())

    job = await e.orchestrator.create_job("p1", TriggerType.MANUAL, trigger_ref="main")

    assert_eq(job.status, JobStatus.PENDING)
    assert_eq(job.pipeline_id, "p1")
    stored = await _job(e.db, job.id)
    assert_eq(stored.status, JobStatus.PENDING)


@test(mark="medium")
async def test_clean_coordinator_exit_reaches_success() -> None:
    e = await load_fixture(env())
    created = await e.orchestrator.create_job("p1", TriggerType.MANUAL)

    job = await e.orchestrator.run_job(created.id)

    assert_eq(job.status, JobStatus.SUCCESS)
    assert_eq(job.failure_reason, None)
    assert job.started_at is not None
    assert job.finished_at is not None
    assert_eq(
        e.runner.method_names,
        ["start_job", "start_coordinator", "wait_for_coordinator", "cleanup_job"],
    )


@test(mark="medium")
async def test_pre_migration_null_egress_starts_default_denied() -> None:
    # A pipeline row created before migration 0027 reads `egress` back as NULL.
    # The job must still start, default-denied (egress False), not crash building
    # a `StartJobRequest` whose `egress` field is a non-optional `bool`.
    e = await load_fixture(env())
    async with e.db.transaction() as tx:
        # Force the out-of-contract NULL a real pre-0027 row reads back as; the
        # `bool` column type cannot express it, so `cast` it deliberately here.
        _ = await tx.execute(
            update(Pipeline)
            .set(Pipeline.egress.to(cast("bool", None)))
            .where(Pipeline.id.eq("p1"))
        )
    created = await e.orchestrator.create_job("p1", TriggerType.MANUAL)

    job = await e.orchestrator.run_job(created.id)

    assert_eq(job.status, JobStatus.SUCCESS)
    start = next(c for c in e.runner.calls if c.method == "start_job")
    request = start.args[0]
    assert isinstance(request, StartJobRequest)
    assert_eq(request.egress, False)


@test(mark="medium")
async def test_requested_limits_flow_into_start_request() -> None:
    # The pipeline's persisted limit columns reach the runner's StartJobRequest;
    # the runner (not the orchestrator) clamps them against the ceiling.
    e = await load_fixture(env())
    async with e.db.transaction() as tx:
        _ = await tx.execute(
            update(Pipeline)
            .set(Pipeline.limit_cpu.to(1.5))
            .set(Pipeline.limit_memory_mb.to(1024))
            .set(Pipeline.limit_pids.to(256))
            .where(Pipeline.id.eq("p1"))
        )
    created = await e.orchestrator.create_job("p1", TriggerType.MANUAL)

    _ = await e.orchestrator.run_job(created.id)

    start = next(c for c in e.runner.calls if c.method == "start_job")
    request = start.args[0]
    assert isinstance(request, StartJobRequest)
    assert_eq(request.limits, ResourceRequest(cpu=1.5, memory_mb=1024, pids=256))


@test(mark="medium")
async def test_job_timeout_is_clamped_to_ceiling() -> None:
    # The orchestrator enforces the wall-clock timeout, so it clamps the pipeline's
    # `max_duration_seconds` to the ceiling max before building the request.
    ceiling = ResourceCeiling(max=ResourceRequest(timeout_seconds=900))
    async with _make_env(FakeRunner(), limits=ceiling) as e:
        async with e.db.transaction() as tx:
            _ = await tx.execute(
                update(Pipeline)
                .set(Pipeline.max_duration_seconds.to(7200))
                .where(Pipeline.id.eq("p1"))
            )
        created = await e.orchestrator.create_job("p1", TriggerType.MANUAL)

        _ = await e.orchestrator.run_job(created.id)

        start = next(c for c in e.runner.calls if c.method == "start_job")
        request = start.args[0]
        assert isinstance(request, StartJobRequest)
        assert_eq(request.max_duration_seconds, 900)


@test(mark="medium")
async def test_coordinator_env_carries_rpc_connection() -> None:
    e = await load_fixture(env())
    created = await e.orchestrator.create_job("p1", TriggerType.MANUAL)

    _ = await e.orchestrator.run_job(created.id)

    start = next(c for c in e.runner.calls if c.method == "start_coordinator")
    env_arg = start.args[1]
    assert isinstance(env_arg, dict)
    assert_eq(env_arg["DANUBE_RPC_ADDRESS"], RPC_ADDRESS)
    assert_eq(env_arg["DANUBE_JOB_ID"], created.id)
    # A non-empty job-scoped token is injected for the Coordinator to authenticate.
    assert env_arg["DANUBE_RPC_TOKEN"]


@test(mark="medium")
async def test_coordinator_crash_marks_failure_and_cleans_up() -> None:
    runner = FakeRunner(coordinator_exit_code=1)
    async with _make_env(runner) as e:
        created = await e.orchestrator.create_job("p1", TriggerType.MANUAL)

        job = await e.orchestrator.run_job(created.id)

        assert_eq(job.status, JobStatus.FAILURE)
        assert job.failure_reason is not None
        assert "1" in job.failure_reason
        assert "cleanup_job" in runner.method_names


@test(mark="medium")
async def test_timeout_stops_and_cleans_up() -> None:
    runner = FakeRunner(coordinator=_sleep_forever)
    async with _make_env(runner) as e:
        await _seed_pipeline(e.db, "p_fast", max_duration_seconds=1)
        created = await e.orchestrator.create_job("p_fast", TriggerType.CRON)

        job = await e.orchestrator.run_job(created.id)

        assert_eq(job.status, JobStatus.TIMEOUT)
        assert job.failure_reason is not None
        assert "max_duration_seconds=1" in job.failure_reason
        assert_eq(
            runner.method_names,
            [
                "start_job",
                "start_coordinator",
                "wait_for_coordinator",
                "stop_job",
                "cleanup_job",
            ],
        )


@test(mark="medium")
async def test_start_job_failure_marks_failure_without_cleanup() -> None:
    runner = _FailingStartRunner()
    async with _make_env(runner) as e:
        created = await e.orchestrator.create_job("p1", TriggerType.MANUAL)

        job = await e.orchestrator.run_job(created.id)

        assert_eq(job.status, JobStatus.FAILURE)
        assert job.failure_reason is not None
        assert "create job environment" in job.failure_reason
        # No environment exists: no coordinator, no cleanup (left to the reaper).
        assert_eq(runner.method_names, ["start_job"])


@test(mark="medium")
async def test_rerunning_a_terminal_job_is_an_illegal_transition() -> None:
    e = await load_fixture(env())
    created = await e.orchestrator.create_job("p1", TriggerType.MANUAL)
    _ = await e.orchestrator.run_job(created.id)

    with assert_raises(InvalidTransition):
        _ = await e.orchestrator.run_job(created.id)


@test(mark="medium")
async def test_log_writer_appends_and_reports_offsets() -> None:
    data_dir = Path(tempfile.mkdtemp(prefix="danube-log-"))
    try:
        path = data_dir / "logs" / "job.log"
        async with LogWriter(path) as log:
            first = await log.write("hello")
            second = await log.write("world!")
        assert_eq(first, (0, 5))
        assert_eq(second, (5, 11))
        assert_eq(await anyio.Path(path).read_bytes(), b"helloworld!")

        async with LogWriter(path) as log:
            third = await log.write("!")
        assert_eq(third, (11, 12))
        assert_eq(await anyio.Path(path).read_bytes(), b"helloworld!!")
    finally:
        shutil.rmtree(data_dir, ignore_errors=True)


@test(mark="medium")
async def test_log_writer_write_outside_context_raises() -> None:
    data_dir = Path(tempfile.mkdtemp(prefix="danube-log-"))
    try:
        writer = LogWriter(data_dir / "logs" / "job.log")
        with assert_raises(LogWriterError):
            await writer.write("nope")
    finally:
        shutil.rmtree(data_dir, ignore_errors=True)
