"""End-to-end job execution in-process: orchestrator -> RPC -> runner -> log.

This is the gate-blocking proof for issue #18's vertical slice, with no Podman.
A real `JobOrchestrator` drives a `ControlPlane` and the FastAPI app (served via
`httpx.ASGITransport`); the Coordinator is simulated by `FakeRunner` running the
real Coordinator entrypoint (`danube.coordinator.run`) against a real `DanubeClient`
pointed at that ASGI transport. So each `step.run()` travels the real
`/rpc/run-step` path, execs in the FakeRunner "Worker", is logged to disk, and its
exit code flows back to the pipeline — exactly the loop a Podman job would run.

The Podman-backed variant lives in `test_job_run_podman.py` and skips without a
socket.
"""

import shutil
import tempfile
from collections.abc import AsyncGenerator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

import anyio
import httpx
from snekql.sqlite import Database, Fetched, insert, select
from snektest import assert_eq, fixture, load_fixture, test

from danube import coordinator
from danube.api import create_app
from danube.db import open_database
from danube.db.models import Job, Pipeline, Step, Team
from danube.domain.enums import JobStatus, StepStatus, TriggerType
from danube.domain.runner_types import ExecResult
from danube.orchestrator import JobOrchestrator
from danube.rpc import ControlPlane
from danube.runner import FakeRunner
from danube.sdk import DanubeClient, JobContext
from danube.sdk.client import ENV_JOB_ID, ENV_RPC_TOKEN
from danube.security import SecretCipher, SecretService, generate_key

RPC_ADDRESS = "http://master.test:9000"


async def two_step_pipeline(danube: DanubeClient) -> None:
    """A minimal pipeline: two Worker steps, then report success."""
    _ = await danube.step.run("echo one", name="one")
    _ = await danube.step.run("echo two", name="two")
    _ = await danube.status.report("success")


async def failing_pipeline(danube: DanubeClient) -> None:
    """A pipeline whose first step fails; the second must never run."""
    _ = await danube.step.run("false", name="one")
    _ = await danube.step.run("echo two", name="two")
    _ = await danube.status.report("success")


async def crashing_pipeline(danube: DanubeClient) -> None:
    """A pipeline that runs one step then raises before reporting status."""
    _ = await danube.step.run("echo one", name="one")
    msg = "boom"
    raise RuntimeError(msg)


async def hanging_pipeline(danube: DanubeClient) -> None:
    _ = await danube.step.run("echo one", name="one")
    await anyio.sleep_forever()


def context_capturing_pipeline(
    captured: dict[str, str | None],
) -> coordinator.Pipeline:
    """Build a pipeline that records `danube.context` fields then reports success."""

    async def pipeline(danube: DanubeClient) -> None:
        captured["pipeline"] = danube.context.pipeline
        captured["trigger_type"] = danube.context.trigger_type
        captured["trigger_ref"] = danube.context.trigger_ref
        captured["branch"] = danube.context.branch
        captured["sha"] = danube.context.sha
        _ = await danube.status.report("success")

    return pipeline


@dataclass
class Harness:
    db: Database
    runner: FakeRunner
    orchestrator: JobOrchestrator
    data_dir: Path
    pipeline: coordinator.Pipeline | None = field(default=None)
    http: httpx.AsyncClient | None = field(default=None)

    def log_path(self, job_id: str) -> Path:
        return self.data_dir / "logs" / f"{job_id}.log"


async def _seed_pipeline(db: Database, max_duration_seconds: int = 3600) -> None:
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
                    max_duration_seconds=max_duration_seconds,
                )
            )
        )


@asynccontextmanager
async def _make_harness(max_duration_seconds: int = 3600) -> AsyncGenerator[Harness]:
    data_dir = Path(tempfile.mkdtemp(prefix="danube-jobrun-"))
    db = await open_database(":memory:")
    # Bootstrap-then-assign: the orchestrator needs `harness` (its `program`
    # closure reads `harness.http`/`harness.pipeline`), so the field is seeded
    # `None` here and assigned below once the http client exists. The ignore is
    # the test-only cost of that cycle, not a production typing relaxation.
    harness = Harness(db=db, runner=FakeRunner(), orchestrator=None, data_dir=data_dir)  # type: ignore[arg-type]

    async def program(env: Mapping[str, str]) -> int:
        assert harness.http is not None
        assert harness.pipeline is not None
        client = DanubeClient(
            harness.http,
            env[ENV_JOB_ID],
            env[ENV_RPC_TOKEN],
            context=JobContext.from_mapping(env),
        )
        return await coordinator.run(client, harness.pipeline)

    runner = FakeRunner(coordinator=program)
    harness.runner = runner
    control_plane = ControlPlane(
        runner,
        db,
        data_dir,
        secret_service=SecretService(db, SecretCipher(generate_key())),
    )
    app = create_app(db, control_plane=control_plane)
    transport = httpx.ASGITransport(app=app)
    try:
        await _seed_pipeline(db, max_duration_seconds)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://master"
        ) as http:
            harness.http = http
            harness.orchestrator = JobOrchestrator(
                runner, db, data_dir, control_plane, rpc_address=RPC_ADDRESS
            )
            yield harness
    finally:
        await db.close()
        shutil.rmtree(data_dir, ignore_errors=True)


@fixture
async def harness() -> AsyncGenerator[Harness]:
    async with _make_harness() as resources:
        yield resources


async def _steps(db: Database, job_id: str) -> list[Step[Fetched]]:
    async with db.transaction() as tx:
        rows = await tx.fetch_all(select(Step).where(Step.job_id.eq(job_id)))
    return sorted(rows, key=lambda step: step.sequence)


async def _job(db: Database, job_id: str) -> Job[Fetched]:
    async with db.transaction() as tx:
        return await tx.fetch_one(select(Job).where(Job.id.eq(job_id)))


@test(mark="medium")
async def test_two_step_pipeline_runs_to_success() -> None:
    h = await load_fixture(harness())
    h.pipeline = two_step_pipeline
    h.runner.script_command(
        "echo one", ExecResult(exit_code=0, stdout="one\n", stderr="")
    )
    h.runner.script_command(
        "echo two", ExecResult(exit_code=0, stdout="two\n", stderr="")
    )
    created = await h.orchestrator.create_job("p1", TriggerType.MANUAL)

    job = await h.orchestrator.run_job(created.id)

    assert_eq(job.status, JobStatus.SUCCESS)
    # Both steps exec'd in the Worker, in order, and were recorded.
    steps = await _steps(h.db, created.id)
    assert_eq([s.name for s in steps], ["one", "two"])
    assert_eq([s.status for s in steps], [StepStatus.SUCCESS, StepStatus.SUCCESS])
    assert_eq([s.exit_code for s in steps], [0, 0])
    # Output landed on disk.
    logged = await anyio.Path(h.log_path(created.id)).read_text()
    assert "one" in logged
    assert "two" in logged


@test(mark="medium")
async def test_failing_step_fails_job_and_skips_later_steps() -> None:
    h = await load_fixture(harness())
    h.pipeline = failing_pipeline
    h.runner.script_command("false", ExecResult(exit_code=2, stdout="", stderr="nope"))
    created = await h.orchestrator.create_job("p1", TriggerType.MANUAL)

    job = await h.orchestrator.run_job(created.id)

    assert_eq(job.status, JobStatus.FAILURE)
    # The second step never ran.
    steps = await _steps(h.db, created.id)
    assert_eq([s.name for s in steps], ["one"])
    assert_eq(steps[0].status, StepStatus.FAILURE)
    assert "cleanup_job" in h.runner.method_names


@test(mark="medium")
async def test_coordinator_crash_fails_job_and_cleans_up() -> None:
    h = await load_fixture(harness())
    h.pipeline = crashing_pipeline
    h.runner.script_command(
        "echo one", ExecResult(exit_code=0, stdout="one\n", stderr="")
    )
    created = await h.orchestrator.create_job("p1", TriggerType.MANUAL)

    job = await h.orchestrator.run_job(created.id)

    assert_eq(job.status, JobStatus.FAILURE)
    # The one step that did run is recorded; cleanup ran.
    steps = await _steps(h.db, created.id)
    assert_eq([s.name for s in steps], ["one"])
    assert "cleanup_job" in h.runner.method_names


@test(mark="medium")
async def test_pipeline_reads_webhook_job_context() -> None:
    h = await load_fixture(harness())
    captured: dict[str, str | None] = {}
    h.pipeline = context_capturing_pipeline(captured)
    created = await h.orchestrator.create_job(
        "p1", TriggerType.WEBHOOK, trigger_ref="main/abc123"
    )

    job = await h.orchestrator.run_job(created.id)

    assert_eq(job.status, JobStatus.SUCCESS)
    # `pipeline` is the pipeline name; the ref decomposes into branch/sha.
    assert_eq(captured["pipeline"], "demo")
    assert_eq(captured["trigger_type"], TriggerType.WEBHOOK.value)
    assert_eq(captured["trigger_ref"], "main/abc123")
    assert_eq(captured["branch"], "main")
    assert_eq(captured["sha"], "abc123")


@test(mark="medium")
async def test_pipeline_reads_manual_job_context_without_ref() -> None:
    h = await load_fixture(harness())
    captured: dict[str, str | None] = {}
    h.pipeline = context_capturing_pipeline(captured)
    created = await h.orchestrator.create_job("p1", TriggerType.MANUAL)

    job = await h.orchestrator.run_job(created.id)

    assert_eq(job.status, JobStatus.SUCCESS)
    assert_eq(captured["pipeline"], "demo")
    assert_eq(captured["trigger_type"], TriggerType.MANUAL.value)
    # A manual job carries no Git ref, so branch/sha surface as None cleanly.
    assert_eq(captured["trigger_ref"], None)
    assert_eq(captured["branch"], None)
    assert_eq(captured["sha"], None)


@test(mark="medium")
async def test_timeout_fails_job_and_cleans_up() -> None:
    async with _make_harness(max_duration_seconds=1) as h:
        h.pipeline = hanging_pipeline
        h.runner.script_command(
            "echo one", ExecResult(exit_code=0, stdout="one\n", stderr="")
        )
        created = await h.orchestrator.create_job("p1", TriggerType.MANUAL)

        job = await h.orchestrator.run_job(created.id)

        assert_eq(job.status, JobStatus.TIMEOUT)
        assert "stop_job" in h.runner.method_names
        assert "cleanup_job" in h.runner.method_names
