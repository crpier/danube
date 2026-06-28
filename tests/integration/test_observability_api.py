"""Integration tests for observability: /metrics, /health/ready, logs, tracing.

A real `JobOrchestrator` drives a job through `FakeRunner` against the FastAPI app
(served via `httpx.ASGITransport`), sharing one `Metrics` registry between the
orchestrator and the `/metrics` endpoint so the exposition reflects the run.
"""

import json
import logging
import shutil
import tempfile
from collections.abc import AsyncGenerator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from snekql.sqlite import Database, insert
from snektest import assert_eq, fixture, load_fixture, test

from danube import coordinator
from danube.api import create_app
from danube.db import open_database
from danube.db.models import Pipeline, Team
from danube.domain.enums import TriggerType
from danube.domain.runner_types import RunnerHealth
from danube.observability import Metrics, Tracer
from danube.observability.logging import JsonLogFormatter
from danube.observability.readiness import run_checks
from danube.orchestrator import JobOrchestrator
from danube.rpc import ControlPlane
from danube.runner import FakeRunner
from danube.sdk import DanubeClient
from danube.sdk.client import ENV_JOB_ID, ENV_RPC_TOKEN
from danube.security import SecretCipher, SecretService, generate_key

RPC_ADDRESS = "http://master.test:9000"


async def one_step_pipeline(danube: DanubeClient) -> None:
    _ = await danube.step.run("echo hi", name="hi")
    _ = await danube.status.report("success")


@dataclass
class Harness:
    db: Database
    runner: FakeRunner
    orchestrator: JobOrchestrator
    metrics: Metrics
    http: httpx.AsyncClient
    pipeline: coordinator.Pipeline | None = field(default=None)


async def _seed_pipeline(db: Database) -> None:
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


@asynccontextmanager
async def _make_harness() -> AsyncGenerator[Harness]:
    data_dir = Path(tempfile.mkdtemp(prefix="danube-obs-"))
    db = await open_database(":memory:")
    metrics = Metrics()
    holder: dict[str, Harness] = {}

    async def program(env: Mapping[str, str]) -> int:
        harness = holder["harness"]
        assert harness.pipeline is not None
        client = DanubeClient(harness.http, env[ENV_JOB_ID], env[ENV_RPC_TOKEN])
        return await coordinator.run(client, harness.pipeline)

    runner = FakeRunner(coordinator=program)
    control_plane = ControlPlane(
        runner,
        db,
        data_dir,
        secret_service=SecretService(db, SecretCipher(generate_key())),
    )
    app = create_app(db, control_plane=control_plane, metrics=metrics, runner=runner)
    transport = httpx.ASGITransport(app=app)
    try:
        await _seed_pipeline(db)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://master"
        ) as http:
            orchestrator = JobOrchestrator(
                runner,
                db,
                data_dir,
                control_plane,
                rpc_address=RPC_ADDRESS,
                metrics=metrics,
            )
            harness = Harness(
                db=db,
                runner=runner,
                orchestrator=orchestrator,
                metrics=metrics,
                http=http,
            )
            holder["harness"] = harness
            yield harness
    finally:
        await db.close()
        shutil.rmtree(data_dir, ignore_errors=True)


@fixture
async def harness() -> AsyncGenerator[Harness]:
    async with _make_harness() as resources:
        yield resources


def _series(text: str) -> dict[str, float]:
    out: dict[str, float] = {}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        name, _, value = line.rpartition(" ")
        out[name] = float(value)
    return out


@test(mark="medium")
async def test_metrics_endpoint_increments_across_a_job_run() -> None:
    h = await load_fixture(harness())
    h.pipeline = one_step_pipeline
    created = await h.orchestrator.create_job("p1", TriggerType.MANUAL)

    _ = await h.orchestrator.run_job(created.id)

    response = await h.http.get("/metrics")
    assert_eq(response.status_code, 200)
    assert response.headers["content-type"].startswith("text/plain")
    series = _series(response.text)
    assert_eq(series['danube_jobs_total{status="success",pipeline="p1"}'], 1.0)
    # The gauge went up during the run and back to zero after it.
    assert_eq(series["danube_active_jobs"], 0.0)
    assert_eq(series['danube_job_duration_seconds_count{pipeline="p1"}'], 1.0)


@test(mark="medium")
async def test_job_run_emits_valid_json_logs() -> None:
    h = await load_fixture(harness())
    h.pipeline = one_step_pipeline
    captured: list[str] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            captured.append(self.format(record))

    handler = _Capture()
    handler.setFormatter(JsonLogFormatter())
    logger = logging.getLogger("danube.orchestrator")
    logger.addHandler(handler)
    previous_level = logger.level
    logger.setLevel(logging.INFO)
    try:
        created = await h.orchestrator.create_job("p1", TriggerType.MANUAL)
        _ = await h.orchestrator.run_job(created.id)
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)

    started = [json.loads(line) for line in captured]
    started = [entry for entry in started if entry["event"] == "job_started"]
    assert_eq(len(started), 1)
    entry = started[0]
    for required in ("timestamp", "level", "logger", "event", "job_id", "pipeline"):
        assert required in entry, f"missing field {required!r}"
    assert_eq(entry["pipeline"], "p1")
    assert_eq(entry["trigger_type"], TriggerType.MANUAL)


@test(mark="medium")
async def test_ready_reports_runner_checks_ok() -> None:
    h = await load_fixture(harness())

    response = await h.http.get("/health/ready")

    assert_eq(response.status_code, 200)
    body = response.json()
    assert_eq(body["status"], "ready")
    assert_eq(
        body["checks"],
        {"database": "ok", "runner": "ok", "container_runtime": "ok"},
    )


@test(mark="medium")
async def test_ready_503_when_container_runtime_unhealthy() -> None:
    db = await open_database(":memory:")
    try:
        unhealthy = FakeRunner(
            health_result=RunnerHealth(healthy=False, runtime="podman")
        )
        checks = await run_checks(db, unhealthy)
        assert_eq(checks["container_runtime"], "error")

        app = create_app(db, runner=unhealthy)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://master"
        ) as http:
            response = await http.get("/health/ready")
        assert_eq(response.status_code, 503)
        assert_eq(response.json()["checks"]["container_runtime"], "error")
    finally:
        await db.close()


@test(mark="medium")
async def test_tracing_enabled_serves_requests_without_a_collector() -> None:
    db = await open_database(":memory:")
    try:
        tracer = Tracer(enabled=True, endpoint="http://127.0.0.1:4317")
        app = create_app(db, tracer=tracer)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://master"
        ) as http:
            health = await http.get("/health")
            metrics = await http.get("/metrics")
        assert_eq(health.status_code, 200)
        assert_eq(metrics.status_code, 200)
    finally:
        await db.close()
