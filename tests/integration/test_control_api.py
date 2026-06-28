"""Integration tests for the control-plane HTTP API.

Everything runs in-process: a real `JobOrchestrator` + `JobManager` drive a real
`ControlPlane`, and the FastAPI app is served through `httpx.ASGITransport`. The
Coordinator is simulated by `FakeRunner` running the real Coordinator entrypoint
against a `DanubeClient` pointed back at the same ASGI transport, so a triggered
run travels the real `/rpc/*` path, logs to disk, and is finalized — exactly the
loop a Podman job would run. No uvicorn socket and no Podman are involved.
"""

import shutil
import tempfile
from collections.abc import AsyncGenerator, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

import anyio
import httpx
from snekql.sqlite import Database, insert
from snektest import assert_eq, fixture, load_fixture, test

from danube import coordinator
from danube.api import create_app
from danube.db import open_database
from danube.db.models import Pipeline, Team
from danube.domain.enums import JobStatus
from danube.domain.lifecycle import TERMINAL_STATES
from danube.domain.runner_types import ExecResult
from danube.orchestrator import JobManager, JobOrchestrator
from danube.rpc import ControlPlane
from danube.runner import FakeRunner
from danube.sdk import DanubeClient
from danube.sdk.client import ENV_JOB_ID, ENV_RPC_TOKEN

RPC_ADDRESS = "http://master.test:9000"


async def two_step_pipeline(danube: DanubeClient) -> None:
    """Two Worker steps, then report success."""
    _ = await danube.step.run("echo one", name="one")
    _ = await danube.step.run("echo two", name="two")
    _ = await danube.status.report("success")


async def hanging_pipeline(danube: DanubeClient) -> None:
    """Run one step, then block forever so the job stays `running` until cancelled."""
    _ = await danube.step.run("echo one", name="one")
    await anyio.sleep_forever()


@dataclass
class Harness:
    db: Database
    runner: FakeRunner
    http: httpx.AsyncClient | None = field(default=None)
    pipeline: coordinator.Pipeline | None = field(default=None)

    @property
    def client(self) -> httpx.AsyncClient:
        assert self.http is not None
        return self.http


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
async def _make_harness(
    max_duration_seconds: int = 3600,
) -> AsyncGenerator[Harness]:
    data_dir = Path(tempfile.mkdtemp(prefix="danube-control-"))
    db = await open_database(":memory:")
    # The Coordinator program reads the live http client and pipeline off the
    # harness; both are seeded after the client is built (the http field starts
    # `None` and the test sets `harness.pipeline`), so the closure reads them
    # lazily at run time rather than at construction.
    harness = Harness(db=db, runner=FakeRunner())

    async def program(env: Mapping[str, str]) -> int:
        assert harness.http is not None
        assert harness.pipeline is not None
        client = DanubeClient(harness.http, env[ENV_JOB_ID], env[ENV_RPC_TOKEN])
        return await coordinator.run(client, harness.pipeline)

    runner = FakeRunner(coordinator=program)
    harness.runner = runner
    control_plane = ControlPlane(runner, db, data_dir)
    orchestrator = JobOrchestrator(
        runner, db, data_dir, control_plane, rpc_address=RPC_ADDRESS
    )
    try:
        await _seed_pipeline(db, max_duration_seconds)
        async with JobManager(orchestrator) as manager:
            app = create_app(db, control_plane=control_plane, job_manager=manager)
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://master"
            ) as http:
                harness.http = http
                yield harness
    finally:
        await db.close()
        shutil.rmtree(data_dir, ignore_errors=True)


@fixture
async def harness() -> AsyncGenerator[Harness]:
    async with _make_harness() as resources:
        yield resources


async def _wait_for(
    http: httpx.AsyncClient,
    job_id: str,
    predicate: Callable[[str], bool],
    timeout_seconds: float = 5.0,
) -> dict[str, object]:
    """Poll the job endpoint until `predicate(status)` holds, then return the body."""
    with anyio.fail_after(timeout_seconds):
        while True:
            response = await http.get(f"/api/v1/jobs/{job_id}")
            body = response.json()
            if predicate(body["status"]):
                return body
            await anyio.sleep(0.01)


def _is_terminal(status: str) -> bool:
    return JobStatus(status) in TERMINAL_STATES


@test(mark="medium")
async def test_trigger_runs_job_to_success() -> None:
    h = await load_fixture(harness())
    h.pipeline = two_step_pipeline
    h.runner.script_command(
        "echo one", ExecResult(exit_code=0, stdout="one\n", stderr="")
    )
    h.runner.script_command(
        "echo two", ExecResult(exit_code=0, stdout="two\n", stderr="")
    )

    triggered = await h.client.post("/api/v1/pipelines/p1/run")

    assert_eq(triggered.status_code, 202)
    job_id = triggered.json()["id"]
    assert_eq(triggered.json()["status"], JobStatus.PENDING)
    final = await _wait_for(h.client, job_id, _is_terminal)
    assert_eq(final["status"], JobStatus.SUCCESS)


@test(mark="medium")
async def test_trigger_unknown_pipeline_is_404() -> None:
    h = await load_fixture(harness())

    response = await h.client.post("/api/v1/pipelines/nope/run")

    assert_eq(response.status_code, 404)


@test(mark="medium")
async def test_cancel_running_job_ends_cancelled_and_cleans_up() -> None:
    h = await load_fixture(harness())
    h.pipeline = hanging_pipeline
    h.runner.script_command(
        "echo one", ExecResult(exit_code=0, stdout="one\n", stderr="")
    )
    triggered = await h.client.post("/api/v1/pipelines/p1/run")
    job_id = triggered.json()["id"]
    # Wait until the job is actually running before cancelling it.
    _ = await _wait_for(h.client, job_id, lambda s: s == JobStatus.RUNNING)

    cancelled = await h.client.post(f"/api/v1/jobs/{job_id}/cancel")

    assert_eq(cancelled.status_code, 202)
    final = await _wait_for(h.client, job_id, _is_terminal)
    assert_eq(final["status"], JobStatus.CANCELLED)
    assert "stop_job" in h.runner.method_names
    assert "cleanup_job" in h.runner.method_names


@test(mark="medium")
async def test_cancel_finished_job_is_409() -> None:
    h = await load_fixture(harness())
    h.pipeline = two_step_pipeline
    triggered = await h.client.post("/api/v1/pipelines/p1/run")
    job_id = triggered.json()["id"]
    _ = await _wait_for(h.client, job_id, _is_terminal)

    response = await h.client.post(f"/api/v1/jobs/{job_id}/cancel")

    assert_eq(response.status_code, 409)


@test(mark="medium")
async def test_cancel_unknown_job_is_404() -> None:
    h = await load_fixture(harness())

    response = await h.client.post("/api/v1/jobs/nope/cancel")

    assert_eq(response.status_code, 404)


@test(mark="medium")
async def test_stream_logs_yields_job_log_lines() -> None:
    h = await load_fixture(harness())
    h.pipeline = two_step_pipeline
    h.runner.script_command(
        "echo one", ExecResult(exit_code=0, stdout="one\n", stderr="")
    )
    h.runner.script_command(
        "echo two", ExecResult(exit_code=0, stdout="two\n", stderr="")
    )
    triggered = await h.client.post("/api/v1/pipelines/p1/run")
    job_id = triggered.json()["id"]

    with anyio.fail_after(5.0):
        async with h.client.stream(
            "GET", f"/api/v1/jobs/{job_id}/logs/stream"
        ) as response:
            assert_eq(response.status_code, 200)
            lines = [line async for line in response.aiter_lines()]

    body = "\n".join(lines)
    assert "data: one" in body
    assert "data: two" in body
    assert "event: end" in body


@test(mark="medium")
async def test_stream_logs_unknown_job_is_404() -> None:
    h = await load_fixture(harness())

    response = await h.client.get("/api/v1/jobs/nope/logs/stream")

    assert_eq(response.status_code, 404)
