"""Tests for the Master Core `JobOrchestrator` and `LogWriter`.

Everything runs against `FakeRunner` (or a small blocking/raising subclass) and an
in-memory snekql database, with logs written to a temporary data directory. No
Podman, no RPC.
"""

import shutil
import tempfile
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

import anyio
from snekql.sqlite import Database, Fetched, insert, select
from snektest import assert_eq, assert_raises, fixture, load_fixture, test

from danube.db import open_database
from danube.db.models import Job, Pipeline, Step, Team
from danube.domain.enums import JobStatus, StepStatus, TriggerType
from danube.domain.lifecycle import InvalidTransition
from danube.domain.runner_types import (
    ExecResult,
    ExecStepRequest,
    JobHandle,
)
from danube.orchestrator import JobOrchestrator, LogWriter, StepSpec
from danube.runner import FakeRunner, RecordedCall


class _RunnerExplodedError(Exception):
    """Raised by `_ExplodingRunner.exec_step` to simulate a runner failure."""


class _ExplodingRunner(FakeRunner):
    """A runner whose `exec_step` always fails after recording the call."""

    async def exec_step(self, job: JobHandle, request: ExecStepRequest) -> ExecResult:
        self.calls.append(RecordedCall("exec_step", (job, request)))
        raise _RunnerExplodedError


class _BlockingRunner(FakeRunner):
    """A runner whose `exec_step` never returns, to drive the job timeout."""

    async def exec_step(self, job: JobHandle, request: ExecStepRequest) -> ExecResult:
        self.calls.append(RecordedCall("exec_step", (job, request)))
        await anyio.sleep_forever()
        raise AssertionError


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
    runner: FakeRunner,
) -> AsyncGenerator[tuple[Database, JobOrchestrator, FakeRunner, Path]]:
    """In-memory DB + temp data dir + orchestrator, seeded with a pipeline."""
    data_dir = Path(tempfile.mkdtemp(prefix="danube-orch-"))
    db = await open_database(":memory:")
    try:
        async with db.transaction() as tx:
            await tx.execute(insert(Team(id="t1", name="team", global_admin=False)))
        await _seed_pipeline(db, "p1")
        orchestrator = JobOrchestrator(runner, db, data_dir)
        yield db, orchestrator, runner, data_dir
    finally:
        await db.close()
        shutil.rmtree(data_dir, ignore_errors=True)


@fixture
async def env() -> AsyncGenerator[tuple[Database, JobOrchestrator, FakeRunner, Path]]:
    runner = FakeRunner()
    async with _make_env(runner) as resources:
        yield resources


async def _steps_for(db: Database, job_id: str) -> list[Step[Fetched]]:
    async with db.transaction() as tx:
        rows = await tx.fetch_all(select(Step).where(Step.job_id.eq(job_id)))
    return sorted(rows, key=lambda step: step.sequence)


@test(mark="medium")
async def test_create_job_persists_pending() -> None:
    db, orchestrator, _, _ = await load_fixture(env())

    job = await orchestrator.create_job("p1", TriggerType.MANUAL, trigger_ref="main")

    assert_eq(job.status, JobStatus.PENDING)
    assert_eq(job.pipeline_id, "p1")
    assert_eq(job.trigger_type, TriggerType.MANUAL)
    assert_eq(job.trigger_ref, "main")
    async with db.transaction() as tx:
        stored = await tx.fetch_one(select(Job).where(Job.id.eq(job.id)))
    assert_eq(stored.status, JobStatus.PENDING)


@test(mark="medium")
async def test_happy_path_all_steps_succeed() -> None:
    db, orchestrator, runner, _ = await load_fixture(env())
    runner.script_sequence(
        ExecResult(exit_code=0, stdout="installed\n", stderr=""),
        ExecResult(exit_code=0, stdout="built\n", stderr=""),
    )
    created = await orchestrator.create_job("p1", TriggerType.MANUAL)

    job = await orchestrator.run_job(
        created.id,
        [
            StepSpec(name="install", command="npm install"),
            StepSpec(name="build", command="npm run build"),
        ],
    )

    assert_eq(job.status, JobStatus.SUCCESS)
    assert_eq(job.failure_reason, None)
    assert job.finished_at is not None
    assert job.started_at is not None
    assert_eq(
        runner.method_names,
        ["start_job", "exec_step", "exec_step", "cleanup_job"],
    )

    steps = await _steps_for(db, created.id)
    assert_eq([s.status for s in steps], [StepStatus.SUCCESS, StepStatus.SUCCESS])
    assert_eq([s.exit_code for s in steps], [0, 0])
    # Offsets are contiguous and match the bytes written for each step.
    assert_eq((steps[0].log_offset_start, steps[0].log_offset_end), (0, 10))
    assert_eq((steps[1].log_offset_start, steps[1].log_offset_end), (10, 16))

    assert job.log_path is not None
    assert_eq(await anyio.Path(job.log_path).read_bytes(), b"installed\nbuilt\n")


@test(mark="medium")
async def test_offsets_match_bytes_written() -> None:
    db, orchestrator, runner, _ = await load_fixture(env())
    runner.script_sequence(
        ExecResult(exit_code=0, stdout="abc", stderr="DE"),
        ExecResult(exit_code=0, stdout="", stderr="zzzz"),
    )
    created = await orchestrator.create_job("p1", TriggerType.MANUAL)

    await orchestrator.run_job(
        created.id,
        [
            StepSpec(name="one", command="one"),
            StepSpec(name="two", command="two"),
        ],
    )

    steps = await _steps_for(db, created.id)
    for step in steps:
        assert step.log_offset_start is not None
        assert step.log_offset_end is not None
    # step one wrote "abc" + "DE" = 5 bytes; step two wrote "zzzz" = 4 bytes.
    assert_eq((steps[0].log_offset_start, steps[0].log_offset_end), (0, 5))
    assert_eq((steps[1].log_offset_start, steps[1].log_offset_end), (5, 9))


@test(mark="medium")
async def test_step_failure_aborts_remaining_steps() -> None:
    db, orchestrator, runner, _ = await load_fixture(env())
    runner.script_sequence(
        ExecResult(exit_code=0, stdout="ok", stderr=""),
        ExecResult(exit_code=2, stdout="", stderr="boom"),
    )
    created = await orchestrator.create_job("p1", TriggerType.MANUAL)

    job = await orchestrator.run_job(
        created.id,
        [
            StepSpec(name="test", command="run tests"),
            StepSpec(name="build", command="build"),
            StepSpec(name="deploy", command="deploy"),
        ],
    )

    assert_eq(job.status, JobStatus.FAILURE)
    assert job.failure_reason is not None
    assert "build" in job.failure_reason
    assert "2" in job.failure_reason
    # The third step is never executed or persisted.
    assert_eq(
        runner.method_names,
        ["start_job", "exec_step", "exec_step", "cleanup_job"],
    )
    steps = await _steps_for(db, created.id)
    assert_eq([s.name for s in steps], ["test", "build"])
    assert_eq([s.status for s in steps], [StepStatus.SUCCESS, StepStatus.FAILURE])


@test(mark="medium")
async def test_check_false_does_not_abort() -> None:
    db, orchestrator, runner, _ = await load_fixture(env())
    runner.script_sequence(
        ExecResult(exit_code=1, stdout="", stderr="warn"),
        ExecResult(exit_code=0, stdout="done", stderr=""),
    )
    created = await orchestrator.create_job("p1", TriggerType.MANUAL)

    job = await orchestrator.run_job(
        created.id,
        [
            StepSpec(name="lint", command="lint", check=False),
            StepSpec(name="build", command="build"),
        ],
    )

    assert_eq(job.status, JobStatus.SUCCESS)
    steps = await _steps_for(db, created.id)
    # The non-zero step is recorded as failed, but the pipeline continues.
    assert_eq([s.status for s in steps], [StepStatus.FAILURE, StepStatus.SUCCESS])
    assert_eq([s.exit_code for s in steps], [1, 0])


@test(mark="medium")
async def test_runner_exception_marks_failure_and_cleans_up() -> None:
    runner = _ExplodingRunner()
    async with _make_env(runner) as (db, orchestrator, _, _):
        created = await orchestrator.create_job("p1", TriggerType.MANUAL)

        job = await orchestrator.run_job(
            created.id, [StepSpec(name="boom", command="boom")]
        )

        assert_eq(job.status, JobStatus.FAILURE)
        assert job.failure_reason is not None
        assert job.failure_reason.startswith("runner failure:")
        assert "cleanup_job" in runner.method_names
        steps = await _steps_for(db, created.id)
        # The in-flight step is finalized as failed, with no exit code.
        assert_eq([s.status for s in steps], [StepStatus.FAILURE])
        assert_eq(steps[0].exit_code, None)


@test(mark="medium")
async def test_timeout_stops_and_cleans_up() -> None:
    runner = _BlockingRunner()
    async with _make_env(runner) as (db, orchestrator, _, _):
        await _seed_pipeline(db, "p_fast", max_duration_seconds=1)
        created = await orchestrator.create_job("p_fast", TriggerType.CRON)

        job = await orchestrator.run_job(
            created.id, [StepSpec(name="slow", command="sleep")]
        )

        assert_eq(job.status, JobStatus.TIMEOUT)
        assert job.failure_reason is not None
        assert "max_duration_seconds=1" in job.failure_reason
        assert_eq(
            runner.method_names,
            ["start_job", "exec_step", "stop_job", "cleanup_job"],
        )
        steps = await _steps_for(db, created.id)
        assert_eq([s.status for s in steps], [StepStatus.FAILURE])


@test(mark="medium")
async def test_rerunning_a_terminal_job_is_an_illegal_transition() -> None:
    _, orchestrator, _, _ = await load_fixture(env())
    created = await orchestrator.create_job("p1", TriggerType.MANUAL)
    _ = await orchestrator.run_job(created.id, [StepSpec(name="ok", command="ok")])

    # The job is already `success`; the lifecycle forbids leaving a terminal state.
    with assert_raises(InvalidTransition):
        await orchestrator.run_job(created.id, [StepSpec(name="again", command="ok")])


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

        # Re-opening resumes from the existing size (append-only).
        async with LogWriter(path) as log:
            third = await log.write("!")
        assert_eq(third, (11, 12))
        assert_eq(await anyio.Path(path).read_bytes(), b"helloworld!!")
    finally:
        shutil.rmtree(data_dir, ignore_errors=True)
