"""Load and validate a Blueprint checkout into an in-memory `Blueprint`.

`parse_blueprint(root)` reads `config.json`, `users.json`, `teams.json`, and
`pipelines/*.json` from a checkout directory, validates each against its Pydantic
schema, then runs the cross-reference checks the reference doc lists (duplicate
names, pipeline -> team, team member -> user, permission team -> team, cron
syntax). Every problem found is collected and raised together as a
`BlueprintValidationError`; a clean parse returns a `Blueprint` aggregate ready to
apply. The parser never touches the database, so an invalid Blueprint cannot
disturb the active configuration.

Missing files are treated as empty (no users/teams/pipelines), which keeps a
minimal or partial Blueprint valid and makes a no-op sync trivial.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from croniter import CroniterError, croniter
from pydantic import ValidationError

from danube.blueprint.errors import BlueprintValidationError
from danube.blueprint.spec import (
    ConfigDocument,
    PipelineDocument,
    TeamDocument,
    UserDocument,
)

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path


@dataclass(frozen=True)
class Blueprint:
    """A validated Blueprint checkout: the desired declarative state."""

    config: ConfigDocument | None
    users: list[UserDocument]
    teams: list[TeamDocument]
    pipelines: list[PipelineDocument]


def parse_blueprint(root: Path) -> Blueprint:
    """Parse and validate the Blueprint rooted at ``root``.

    Raises `BlueprintValidationError` listing every schema or cross-reference
    problem; returns a `Blueprint` when the checkout is clean.
    """
    problems: list[str] = []
    config = _load_config(root, problems)
    users = _load_user_list(root / "users.json", problems)
    teams = _load_team_list(root / "teams.json", problems)
    pipelines = _load_pipelines(root / "pipelines", problems)
    # Cross-reference checks assume well-formed documents, so only run them once
    # every file parsed cleanly. Otherwise a parse failure would cascade into
    # noisy, misleading reference errors.
    if not problems:
        _validate_cross_references(users, teams, pipelines, problems)
    if problems:
        raise BlueprintValidationError(problems)
    return Blueprint(config=config, users=users, teams=teams, pipelines=pipelines)


def _load_json(path: Path, problems: list[str]) -> object | None:
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as error:
        problems.append(f"{path.name}: invalid JSON ({error})")
        return None


def _load_config(root: Path, problems: list[str]) -> ConfigDocument | None:
    path = root / "config.json"
    if not path.is_file():
        return None
    raw = _load_json(path, problems)
    if raw is None:
        return None
    try:
        return ConfigDocument.model_validate(raw)
    except ValidationError as error:
        problems.append(f"config.json: {_format_validation_error(error)}")
        return None


def _load_user_list(path: Path, problems: list[str]) -> list[UserDocument]:
    raw = _load_array(path, problems)
    documents: list[UserDocument] = []
    for index, item in enumerate(raw):
        try:
            documents.append(UserDocument.model_validate(item))
        except ValidationError as error:
            problems.append(f"{path.name}[{index}]: {_format_validation_error(error)}")
    return documents


def _load_team_list(path: Path, problems: list[str]) -> list[TeamDocument]:
    raw = _load_array(path, problems)
    documents: list[TeamDocument] = []
    for index, item in enumerate(raw):
        try:
            documents.append(TeamDocument.model_validate(item))
        except ValidationError as error:
            problems.append(f"{path.name}[{index}]: {_format_validation_error(error)}")
    return documents


def _load_array(path: Path, problems: list[str]) -> list[object]:
    if not path.is_file():
        return []
    raw = _load_json(path, problems)
    if raw is None:
        return []
    if not isinstance(raw, list):
        problems.append(f"{path.name}: expected a JSON array")
        return []
    return cast("list[object]", raw)


def _load_pipelines(directory: Path, problems: list[str]) -> list[PipelineDocument]:
    if not directory.is_dir():
        return []
    documents: list[PipelineDocument] = []
    for path in sorted(directory.glob("*.json")):
        raw = _load_json(path, problems)
        if raw is None:
            continue
        try:
            documents.append(PipelineDocument.model_validate(raw))
        except ValidationError as error:
            problems.append(f"pipelines/{path.name}: {_format_validation_error(error)}")
    return documents


def _validate_cross_references(
    users: list[UserDocument],
    teams: list[TeamDocument],
    pipelines: list[PipelineDocument],
    problems: list[str],
) -> None:
    user_emails = _unique_names(
        (user.spec.email for user in users), "user email", problems
    )
    _ = _unique_names((user.metadata.name for user in users), "user", problems)
    team_names = _unique_names((team.metadata.name for team in teams), "team", problems)
    _ = _unique_names(
        (pipeline.metadata.name for pipeline in pipelines), "pipeline", problems
    )

    for team in teams:
        problems.extend(
            f"team {team.metadata.name!r} references unknown user {member!r}"
            for member in team.spec.members
            if member not in user_emails
        )

    for pipeline in pipelines:
        name = pipeline.metadata.name
        if pipeline.metadata.team not in team_names:
            problems.append(
                f"pipeline {name!r} references unknown team {pipeline.metadata.team!r}"
            )
        problems.extend(
            f"pipeline {name!r} permission references unknown team {permission.team!r}"
            for permission in pipeline.spec.permissions
            if permission.team not in team_names
        )
        for trigger in pipeline.spec.triggers:
            if trigger.on != "cron":
                continue
            if trigger.schedule is None:
                problems.append(
                    f"pipeline {name!r} has a cron trigger with no schedule"
                )
                continue
            if not _is_valid_cron(trigger.schedule):
                problems.append(
                    f"pipeline {name!r} has an invalid cron schedule {trigger.schedule!r}"
                )


def _unique_names(values: Iterable[str], label: str, problems: list[str]) -> set[str]:
    seen: set[str] = set()
    for value in values:
        if value in seen:
            problems.append(f"duplicate {label} {value!r}")
        seen.add(value)
    return seen


def _is_valid_cron(expression: str) -> bool:
    try:
        return croniter.is_valid(expression)
    except CroniterError, ValueError:
        return False


def _format_validation_error(error: ValidationError) -> str:
    parts: list[str] = []
    for detail in error.errors():
        location = ".".join(str(item) for item in detail["loc"]) or "<root>"
        parts.append(f"{location}: {detail['msg']}")
    return ", ".join(parts)
