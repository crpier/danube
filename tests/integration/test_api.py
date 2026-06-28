"""Integration tests for the read-only FastAPI app.

Everything runs in-process against an in-memory snekql database through
`httpx.ASGITransport`; no uvicorn socket is opened. The Master wiring
(`build_app`) is exercised the same way to prove the entrypoint serves a working
`/health`.
"""

from collections.abc import AsyncGenerator

import httpx
from snekql.sqlite import Database, insert
from snektest import assert_eq, assert_raises, fixture, load_fixture, test

from danube.api import create_app
from danube.db import open_database
from danube.db.models import Job, Pipeline, Team
from danube.domain.enums import JobStatus, TriggerType
from danube.master import MasterConfig, _split_bind_address, build_app


async def _seed(db: Database) -> None:
    """Seed one team, two pipelines, and three jobs on the first pipeline."""
    async with db.transaction() as tx:
        await tx.execute(insert(Team(id="t1", name="team", global_admin=False)))
        await tx.execute(
            insert(
                Pipeline(
                    id="p1",
                    name="web",
                    team_id="t1",
                    repo_url="https://example.test/web.git",
                    worker_image="img:web",
                )
            )
        )
        await tx.execute(
            insert(
                Pipeline(
                    id="p2",
                    name="api",
                    team_id="t1",
                    repo_url="https://example.test/api.git",
                    worker_image="img:api",
                )
            )
        )
        for index in range(3):
            await tx.execute(
                insert(
                    Job(
                        id=f"j{index}",
                        pipeline_id="p1",
                        trigger_type=TriggerType.MANUAL,
                        status=JobStatus.PENDING,
                    )
                )
            )


def _client(db: Database) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=create_app(db))
    return httpx.AsyncClient(transport=transport, base_url="http://test")


@fixture
async def client() -> AsyncGenerator[tuple[httpx.AsyncClient, Database]]:
    db = await open_database(":memory:")
    try:
        await _seed(db)
        async with _client(db) as http_client:
            yield http_client, db
    finally:
        await db.close()


@test(mark="medium")
async def test_health_is_ok() -> None:
    http_client, _ = await load_fixture(client())

    response = await http_client.get("/health")

    assert_eq(response.status_code, 200)
    assert_eq(response.json(), {"status": "ok"})


@test(mark="medium")
async def test_ready_reports_database_up() -> None:
    http_client, _ = await load_fixture(client())

    response = await http_client.get("/health/ready")

    assert_eq(response.status_code, 200)
    assert_eq(response.json(), {"status": "ready", "database": True})


@test(mark="medium")
async def test_ready_reports_503_when_database_down() -> None:
    http_client, db = await load_fixture(client())
    # Closing the database makes the readiness probe's query fail.
    await db.close()

    response = await http_client.get("/health/ready")

    assert_eq(response.status_code, 503)
    assert_eq(response.json(), {"status": "unavailable", "database": False})


@test(mark="medium")
async def test_list_jobs_returns_seeded_jobs() -> None:
    http_client, _ = await load_fixture(client())

    response = await http_client.get("/api/v1/jobs")

    assert_eq(response.status_code, 200)
    body = response.json()
    assert_eq(body["total"], 3)
    assert_eq(len(body["items"]), 3)
    assert_eq({item["id"] for item in body["items"]}, {"j0", "j1", "j2"})
    assert_eq(body["items"][0]["status"], JobStatus.PENDING)


@test(mark="medium")
async def test_list_jobs_paginates() -> None:
    http_client, _ = await load_fixture(client())

    first = await http_client.get("/api/v1/jobs", params={"limit": 2, "offset": 0})
    second = await http_client.get("/api/v1/jobs", params={"limit": 2, "offset": 2})

    first_body = first.json()
    second_body = second.json()
    assert_eq(first_body["total"], 3)
    assert_eq(len(first_body["items"]), 2)
    assert_eq(second_body["total"], 3)
    assert_eq(len(second_body["items"]), 1)
    # The two pages cover every job exactly once.
    ids = {item["id"] for item in first_body["items"]} | {
        item["id"] for item in second_body["items"]
    }
    assert_eq(ids, {"j0", "j1", "j2"})


@test(mark="medium")
async def test_list_jobs_rejects_bad_pagination() -> None:
    http_client, _ = await load_fixture(client())

    response = await http_client.get("/api/v1/jobs", params={"limit": 0})

    assert_eq(response.status_code, 422)


@test(mark="medium")
async def test_get_job_returns_detail() -> None:
    http_client, _ = await load_fixture(client())

    response = await http_client.get("/api/v1/jobs/j1")

    assert_eq(response.status_code, 200)
    body = response.json()
    assert_eq(body["id"], "j1")
    assert_eq(body["pipeline_id"], "p1")
    assert_eq(body["trigger_type"], TriggerType.MANUAL)
    assert_eq(body["status"], JobStatus.PENDING)


@test(mark="medium")
async def test_get_unknown_job_is_404() -> None:
    http_client, _ = await load_fixture(client())

    response = await http_client.get("/api/v1/jobs/nope")

    assert_eq(response.status_code, 404)


@test(mark="medium")
async def test_list_pipelines_returns_seeded_pipelines() -> None:
    http_client, _ = await load_fixture(client())

    response = await http_client.get("/api/v1/pipelines")

    assert_eq(response.status_code, 200)
    body = response.json()
    assert_eq(body["total"], 2)
    assert_eq({item["id"] for item in body["items"]}, {"p1", "p2"})


@test(mark="medium")
async def test_get_pipeline_returns_detail() -> None:
    http_client, _ = await load_fixture(client())

    response = await http_client.get("/api/v1/pipelines/p2")

    assert_eq(response.status_code, 200)
    body = response.json()
    assert_eq(body["id"], "p2")
    assert_eq(body["name"], "api")
    assert_eq(body["worker_image"], "img:api")
    assert_eq(body["max_duration_seconds"], 3600)


@test(mark="medium")
async def test_get_unknown_pipeline_is_404() -> None:
    http_client, _ = await load_fixture(client())

    response = await http_client.get("/api/v1/pipelines/nope")

    assert_eq(response.status_code, 404)


@test(mark="medium")
async def test_build_app_serves_health() -> None:
    config = MasterConfig(config_path=None, database_path=":memory:")
    app, db = await build_app(config)
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as http_client:
            response = await http_client.get("/health")
        assert_eq(response.status_code, 200)
    finally:
        await db.close()


@test(mark="fast")
def test_split_bind_address_parses_host_and_port() -> None:
    assert_eq(_split_bind_address("127.0.0.1:8000"), ("127.0.0.1", 8000))


@test(mark="fast")
def test_split_bind_address_rejects_missing_port() -> None:
    with assert_raises(ValueError):
        _ = _split_bind_address("localhost")
