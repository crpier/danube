"""Unit tests for the BlueprintSyncer sync path.

A fake source points the syncer at a directory on disk so the fetch step is
trivial; the focus is the sync contract: a valid checkout applies and returns a
diff, an invalid checkout raises and leaves the previously synced configuration
untouched, a re-sync of unchanged files is a no-op, and applied changes are
logged.
"""

import json
import logging
import tempfile
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

from snekql.sqlite import Database, select
from snektest import assert_eq, assert_raises, fixture, load_fixture, test

from danube.blueprint import BlueprintSyncer, BlueprintValidationError
from danube.db import open_database
from danube.db.models import Pipeline, Team

API = "danube.dev/v1"


class _DirSource:
    """A `BlueprintSource` that always returns the same checkout directory."""

    def __init__(self, root: Path) -> None:
        self._root = root

    async def fetch(self) -> Path:
        return self._root


def _team(name: str) -> dict[str, Any]:
    return {
        "apiVersion": API,
        "kind": "Team",
        "metadata": {"name": name},
        "spec": {"members": []},
    }


def _pipeline(name: str, team: str, *, image: str = "node:20-alpine") -> dict[str, Any]:
    return {
        "apiVersion": API,
        "kind": "Pipeline",
        "metadata": {"name": name, "team": team},
        "spec": {
            "repository": f"https://example.test/{name}.git",
            "worker": {"image": image},
        },
    }


def _write(
    root: Path, teams: list[dict[str, Any]], pipelines: list[dict[str, Any]]
) -> None:
    (root / "teams.json").write_text(json.dumps(teams))
    pipeline_dir = root / "pipelines"
    pipeline_dir.mkdir(exist_ok=True)
    for existing in pipeline_dir.glob("*.json"):
        existing.unlink()
    for pipeline in pipelines:
        (pipeline_dir / f"{pipeline['metadata']['name']}.json").write_text(
            json.dumps(pipeline)
        )


@fixture
async def db() -> AsyncGenerator[Database]:
    database = await open_database(":memory:")
    try:
        yield database
    finally:
        await database.close()


@test(mark="fast")
async def test_sync_applies_a_valid_checkout() -> None:
    database = await load_fixture(db())
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        _write(root, [_team("engineering")], [_pipeline("frontend", "engineering")])
        syncer = BlueprintSyncer(database, _DirSource(root))

        diff = await syncer.sync()

    assert_eq(diff.applied_counts(), {"user": 0, "team": 1, "pipeline": 1})
    async with database.transaction() as tx:
        pipelines = await tx.fetch_all(select(Pipeline).all())
    assert_eq(len(pipelines), 1)


@test(mark="fast")
async def test_invalid_sync_leaves_prior_config_intact() -> None:
    database = await load_fixture(db())
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        _write(root, [_team("engineering")], [_pipeline("frontend", "engineering")])
        syncer = BlueprintSyncer(database, _DirSource(root))
        _ = await syncer.sync()

        # Break a cross-reference: point the pipeline at a team that no longer exists.
        _write(root, [_team("engineering")], [_pipeline("frontend", "ghost-team")])
        with assert_raises(BlueprintValidationError):
            _ = await syncer.sync()

    # The previously synced configuration is untouched.
    async with database.transaction() as tx:
        pipelines = await tx.fetch_all(select(Pipeline).all())
        teams = await tx.fetch_all(select(Team).all())
    assert_eq(len(pipelines), 1)
    assert_eq(pipelines[0].team_id, teams[0].id)


@test(mark="fast")
async def test_resync_after_a_change_applies_only_the_change() -> None:
    database = await load_fixture(db())
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        _write(
            root,
            [_team("engineering")],
            [_pipeline("frontend", "engineering"), _pipeline("backend", "engineering")],
        )
        syncer = BlueprintSyncer(database, _DirSource(root))
        _ = await syncer.sync()

        # A second sync of the same files applies nothing.
        noop = await syncer.sync()
        assert noop.is_empty

        # Change one pipeline; only it is updated.
        _write(
            root,
            [_team("engineering")],
            [
                _pipeline("frontend", "engineering", image="node:22-alpine"),
                _pipeline("backend", "engineering"),
            ],
        )
        diff = await syncer.sync()

    assert_eq(diff.pipelines.updated, ["frontend"])
    assert_eq(diff.applied_counts(), {"user": 0, "team": 0, "pipeline": 1})


@test(mark="fast")
async def test_applied_changes_are_logged() -> None:
    database = await load_fixture(db())
    records: list[logging.LogRecord] = []
    handler = logging.Handler()
    handler.emit = records.append  # type: ignore[method-assign]
    logger = logging.getLogger("danube.blueprint")
    logger.addHandler(handler)
    previous_level = logger.level
    logger.setLevel(logging.INFO)
    try:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            _write(root, [_team("engineering")], [_pipeline("frontend", "engineering")])
            syncer = BlueprintSyncer(database, _DirSource(root))
            _ = await syncer.sync()
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)

    messages = [record.getMessage() for record in records]
    assert any("applied changes" in message for message in messages)
    assert any("pipeline diff" in message for message in messages)
