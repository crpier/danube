"""Unit tests for Blueprint parsing and validation.

These build a Blueprint checkout on disk from plain dicts and assert that a clean
checkout parses into the expected documents while each kind of defect (bad JSON,
schema violation, broken cross-reference, duplicate name, bad cron) is reported as
a `BlueprintValidationError`. No database is involved -- parsing is pure.
"""

import json
import tempfile
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from snektest import assert_eq, assert_raises, test

from danube.blueprint import BlueprintValidationError, parse_blueprint

API = "danube.dev/v1"


def _user(name: str, email: str) -> dict[str, Any]:
    return {
        "apiVersion": API,
        "kind": "User",
        "metadata": {"name": name},
        "spec": {"email": email},
    }


def _team(
    name: str, members: list[str], *, global_admin: bool = False
) -> dict[str, Any]:
    return {
        "apiVersion": API,
        "kind": "Team",
        "metadata": {"name": name},
        "spec": {"members": members, "global_admin": global_admin},
    }


def _pipeline(
    name: str,
    team: str,
    *,
    triggers: list[dict[str, Any]] | None = None,
    permissions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "apiVersion": API,
        "kind": "Pipeline",
        "metadata": {"name": name, "team": team},
        "spec": {
            "repository": f"https://example.test/{name}.git",
            "worker": {"image": "node:20-alpine"},
            "triggers": triggers or [],
            "permissions": permissions or [],
        },
    }


def _write_blueprint(
    root: Path,
    *,
    users: list[dict[str, Any]] | None = None,
    teams: list[dict[str, Any]] | None = None,
    pipelines: list[dict[str, Any]] | None = None,
) -> None:
    if users is not None:
        (root / "users.json").write_text(json.dumps(users))
    if teams is not None:
        (root / "teams.json").write_text(json.dumps(teams))
    if pipelines is not None:
        pipeline_dir = root / "pipelines"
        pipeline_dir.mkdir(exist_ok=True)
        for pipeline in pipelines:
            (pipeline_dir / f"{pipeline['metadata']['name']}.json").write_text(
                json.dumps(pipeline)
            )


@contextmanager
def _checkout() -> Generator[Path]:
    with tempfile.TemporaryDirectory() as raw_dir:
        yield Path(raw_dir)


@test(mark="fast")
def test_valid_blueprint_parses_all_documents() -> None:
    with _checkout() as root:
        _write_blueprint(
            root,
            users=[_user("alice", "alice@example.com")],
            teams=[_team("engineering", ["alice@example.com"])],
            pipelines=[
                _pipeline(
                    "frontend",
                    "engineering",
                    triggers=[{"on": "cron", "schedule": "0 0 * * *"}],
                    permissions=[{"team": "engineering", "level": "admin"}],
                )
            ],
        )

        blueprint = parse_blueprint(root)

    assert_eq(len(blueprint.users), 1)
    assert_eq(blueprint.users[0].spec.email, "alice@example.com")
    assert_eq(len(blueprint.teams), 1)
    assert_eq(len(blueprint.pipelines), 1)
    assert_eq(blueprint.pipelines[0].metadata.team, "engineering")


@test(mark="fast")
def test_empty_checkout_is_valid_and_empty() -> None:
    with _checkout() as root:
        blueprint = parse_blueprint(root)
    assert_eq(blueprint.users, [])
    assert_eq(blueprint.teams, [])
    assert_eq(blueprint.pipelines, [])


@test(mark="fast")
def test_invalid_json_is_reported() -> None:
    with _checkout() as root:
        (root / "users.json").write_text("{not json")
        with assert_raises(BlueprintValidationError):
            _ = parse_blueprint(root)


@test(mark="fast")
def test_missing_required_field_fails_schema() -> None:
    with _checkout() as root:
        bad = _pipeline("frontend", "engineering")
        del bad["spec"]["repository"]
        _write_blueprint(root, teams=[_team("engineering", [])], pipelines=[bad])
        with assert_raises(BlueprintValidationError):
            _ = parse_blueprint(root)


@test(mark="fast")
def test_pipeline_referencing_unknown_team_is_rejected() -> None:
    with _checkout() as root:
        _write_blueprint(
            root,
            teams=[_team("engineering", [])],
            pipelines=[_pipeline("frontend", "ghost-team")],
        )
        with assert_raises(BlueprintValidationError) as caught:
            _ = parse_blueprint(root)
    assert any("ghost-team" in problem for problem in caught.exception.problems)


@test(mark="fast")
def test_team_member_referencing_unknown_user_is_rejected() -> None:
    with _checkout() as root:
        _write_blueprint(
            root,
            users=[_user("alice", "alice@example.com")],
            teams=[_team("engineering", ["ghost@example.com"])],
        )
        with assert_raises(BlueprintValidationError) as caught:
            _ = parse_blueprint(root)
    assert any("ghost@example.com" in problem for problem in caught.exception.problems)


@test(mark="fast")
def test_permission_referencing_unknown_team_is_rejected() -> None:
    with _checkout() as root:
        _write_blueprint(
            root,
            teams=[_team("engineering", [])],
            pipelines=[
                _pipeline(
                    "frontend",
                    "engineering",
                    permissions=[{"team": "qa", "level": "read"}],
                )
            ],
        )
        with assert_raises(BlueprintValidationError):
            _ = parse_blueprint(root)


@test(mark="fast")
def test_duplicate_team_name_is_rejected() -> None:
    with _checkout() as root:
        _write_blueprint(
            root, teams=[_team("engineering", []), _team("engineering", [])]
        )
        with assert_raises(BlueprintValidationError) as caught:
            _ = parse_blueprint(root)
    assert any("duplicate" in problem for problem in caught.exception.problems)


@test(mark="fast")
def test_invalid_cron_schedule_is_rejected() -> None:
    with _checkout() as root:
        _write_blueprint(
            root,
            teams=[_team("engineering", [])],
            pipelines=[
                _pipeline(
                    "frontend",
                    "engineering",
                    triggers=[{"on": "cron", "schedule": "not a cron"}],
                )
            ],
        )
        with assert_raises(BlueprintValidationError):
            _ = parse_blueprint(root)


@test(mark="fast")
def test_invalid_permission_level_fails_schema() -> None:
    with _checkout() as root:
        _write_blueprint(
            root,
            teams=[_team("engineering", [])],
            pipelines=[
                _pipeline(
                    "frontend",
                    "engineering",
                    permissions=[{"team": "engineering", "level": "superuser"}],
                )
            ],
        )
        with assert_raises(BlueprintValidationError):
            _ = parse_blueprint(root)
