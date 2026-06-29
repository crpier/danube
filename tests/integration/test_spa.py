"""Integration tests for serving the built frontend SPA from FastAPI.

These exercise `create_app(..., spa_dir=...)`: when given a directory holding a
built `index.html`, the Master answers `/` and any client-side route with that
shell, serves real files under the directory directly, and never lets the SPA
fallback shadow the JSON API. Everything runs in-process through
`httpx.ASGITransport`.
"""

import tempfile
from collections.abc import AsyncGenerator
from pathlib import Path

import httpx
from snekql.sqlite import Database, insert
from snektest import assert_eq, fixture, load_fixture, test

from danube.api import create_app
from danube.db import open_database
from danube.db.models import Pipeline, Team

_INDEX_HTML = "<!doctype html><html><body><div id='app'></div></body></html>"
_APP_JS = "console.log('danube');\n"


def _build_dist(directory: Path) -> Path:
    """Write a minimal built SPA (index + one asset) into `directory`."""
    (directory / "index.html").write_text(_INDEX_HTML)
    assets = directory / "assets"
    assets.mkdir()
    (assets / "app.js").write_text(_APP_JS)
    return directory


async def _seed(db: Database) -> None:
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


@fixture
async def client() -> AsyncGenerator[httpx.AsyncClient]:
    db = await open_database(":memory:")
    with tempfile.TemporaryDirectory(prefix="danube-spa-") as tmp:
        dist = _build_dist(Path(tmp))
        try:
            await _seed(db)
            app = create_app(db, spa_dir=dist)
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://test"
            ) as http_client:
                yield http_client
        finally:
            await db.close()


@test(mark="medium")
async def test_root_serves_index() -> None:
    http_client = await load_fixture(client())

    response = await http_client.get("/")

    assert_eq(response.status_code, 200)
    assert_eq(response.text, _INDEX_HTML)


@test(mark="medium")
async def test_client_route_falls_back_to_index() -> None:
    http_client = await load_fixture(client())

    response = await http_client.get("/pipelines/p1")

    assert_eq(response.status_code, 200)
    assert_eq(response.text, _INDEX_HTML)


@test(mark="medium")
async def test_asset_is_served_directly() -> None:
    http_client = await load_fixture(client())

    response = await http_client.get("/assets/app.js")

    assert_eq(response.status_code, 200)
    assert_eq(response.text, _APP_JS)


@test(mark="medium")
async def test_api_route_not_shadowed_by_spa() -> None:
    http_client = await load_fixture(client())

    response = await http_client.get("/api/v1/pipelines")

    assert_eq(response.status_code, 200)
    body = response.json()
    assert_eq(body["total"], 1)


@test(mark="medium")
async def test_unknown_api_path_is_404_not_index() -> None:
    http_client = await load_fixture(client())

    response = await http_client.get("/api/v1/does-not-exist")

    assert_eq(response.status_code, 404)


@test(mark="medium")
async def test_no_spa_dir_leaves_root_unmounted() -> None:
    db = await open_database(":memory:")
    try:
        transport = httpx.ASGITransport(app=create_app(db))
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as http_client:
            response = await http_client.get("/")
        assert_eq(response.status_code, 404)
    finally:
        await db.close()
