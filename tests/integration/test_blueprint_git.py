"""Integration test: BlueprintSyncer over a real Git Blueprint repository.

Builds a throwaway local Git repo with `git` CLI, commits Blueprint files, and
drives a `GitBlueprintSource` + `BlueprintSyncer` against it end to end: the first
sync clones and applies; a second commit that changes one pipeline pulls and
applies only that change. This exercises the real clone/pull path the unit tests
stub out. It skips if `git` is not on PATH.
"""

import asyncio
import json
import shutil
import tempfile
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

from snekql.sqlite import Database, select
from snektest import assert_eq, fixture, load_fixture, test

from danube.blueprint import BlueprintSyncer, GitBlueprintSource
from danube.db import open_database
from danube.db.models import Pipeline

API = "danube.dev/v1"
_GIT = shutil.which("git")


def _team(name: str) -> dict[str, Any]:
    return {
        "apiVersion": API,
        "kind": "Team",
        "metadata": {"name": name},
        "spec": {"members": []},
    }


def _pipeline(name: str, *, image: str) -> dict[str, Any]:
    return {
        "apiVersion": API,
        "kind": "Pipeline",
        "metadata": {"name": name, "team": "engineering"},
        "spec": {
            "repository": f"https://example.test/{name}.git",
            "worker": {"image": image},
        },
    }


async def _git(cwd: Path, *args: str) -> None:
    assert _GIT is not None
    process = await asyncio.create_subprocess_exec(
        _GIT,
        "-C",
        str(cwd),
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await process.communicate()
    if process.returncode != 0:
        message = stderr.decode(errors="replace")
        raise AssertionError(message)


async def _commit_blueprint(repo: Path, image: str) -> None:
    (repo / "teams.json").write_text(json.dumps([_team("engineering")]))
    pipelines = repo / "pipelines"
    pipelines.mkdir(exist_ok=True)
    (pipelines / "frontend.json").write_text(
        json.dumps(_pipeline("frontend", image=image))
    )
    await _git(repo, "add", "-A")
    await _git(repo, "commit", "-m", f"blueprint {image}")


@fixture
async def db() -> AsyncGenerator[Database]:
    database = await open_database(":memory:")
    try:
        yield database
    finally:
        await database.close()


if _GIT is not None:

    @test(mark="medium")
    async def test_git_sync_clones_then_pulls_only_changes() -> None:
        database = await load_fixture(db())
        with tempfile.TemporaryDirectory() as raw:
            base = Path(raw)
            origin = base / "origin"
            origin.mkdir()
            await _git(origin, "init")
            await _git(origin, "config", "user.email", "test@example.com")
            await _git(origin, "config", "user.name", "Test")
            await _commit_blueprint(origin, "node:20-alpine")

            source = GitBlueprintSource(str(origin), base / "checkout")
            syncer = BlueprintSyncer(database, source)

            first = await syncer.sync()
            assert_eq(first.applied_counts(), {"user": 0, "team": 1, "pipeline": 1})

            # A second commit changing the worker image should sync as one update.
            await _commit_blueprint(origin, "node:22-alpine")
            second = await syncer.sync()

        assert_eq(second.pipelines.updated, ["frontend"])
        assert_eq(second.applied_counts(), {"user": 0, "team": 0, "pipeline": 1})
        async with database.transaction() as tx:
            frontend = await tx.fetch_one(
                select(Pipeline).where(Pipeline.name.eq("frontend"))
            )
        assert_eq(frontend.worker_image, "node:22-alpine")
