"""Apply a validated `Blueprint` into SQLite transactionally and report the diff.

`apply_blueprint(db, blueprint)` reconciles the desired declarative state against
the live tables inside a single transaction: it inserts new entities, updates
changed ones, and deletes removed ones, so a re-run after a commit touches only
the entities that actually changed. Anything raised mid-apply rolls the whole
transaction back, leaving the previously active configuration intact.

Identity is derived deterministically from each entity's natural key (a user's
email, a team's or pipeline's name) with UUIDv5, so the same Blueprint always maps
to the same primary keys and re-syncs are stable without an extra lookup.

Ordering respects the foreign keys (which SQLite checks per statement): deletes go
children-before-parents and upserts go parents-before-children. Child-row changes
(team membership, pipeline permissions) are folded into their parent's diff, so a
pipeline whose permissions changed counts as a changed pipeline -- matching the
`blueprint_changes_applied_total{type=pipeline|user|team}` metric.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from snekql.sqlite import CurrentTimestamp, delete, insert, select, update

from danube.db.models import (
    Pipeline,
    PipelinePermission,
    Team,
    TeamMember,
    User,
)

if TYPE_CHECKING:
    from snekql.sqlite import Database, Fetched, Transaction

    from danube.blueprint.parser import Blueprint

# Fixed namespace so a natural key always maps to the same primary key across
# syncs and processes. Generated once; never change it or every id would shift.
_NAMESPACE = uuid.UUID("6f0d6f3a-2d2c-5a7e-9b1f-0a3c5d8e1f24")
_MEMBER_ROLE = "member"


def _entity_id(kind: str, key: str) -> str:
    return str(uuid.uuid5(_NAMESPACE, f"{kind}:{key}"))


@dataclass
class EntityChange:
    """Names of entities of one type added, updated, or removed by a sync."""

    added: list[str] = field(default_factory=list[str])
    updated: list[str] = field(default_factory=list[str])
    removed: list[str] = field(default_factory=list[str])

    @property
    def total(self) -> int:
        return len(self.added) + len(self.updated) + len(self.removed)


@dataclass
class BlueprintDiff:
    """Per-type summary of what a sync applied. Empty means a no-op sync."""

    users: EntityChange = field(default_factory=EntityChange)
    teams: EntityChange = field(default_factory=EntityChange)
    pipelines: EntityChange = field(default_factory=EntityChange)

    @property
    def is_empty(self) -> bool:
        return self.users.total + self.teams.total + self.pipelines.total == 0

    def applied_counts(self) -> dict[str, int]:
        """Counts keyed by the observability metric `type` label."""
        return {
            "user": self.users.total,
            "team": self.teams.total,
            "pipeline": self.pipelines.total,
        }


@dataclass(frozen=True)
class _DesiredUser:
    id: str
    email: str
    name: str


@dataclass(frozen=True)
class _DesiredTeam:
    id: str
    name: str
    global_admin: bool


@dataclass(frozen=True)
class _DesiredPipeline:
    id: str
    name: str
    team_id: str
    repo_url: str
    branch_filter: str | None
    cron_schedule: str | None
    config_path: str
    worker_image: str
    max_duration_seconds: int
    workspace_size_gb: int
    egress: bool


async def apply_blueprint(db: Database, blueprint: Blueprint) -> BlueprintDiff:
    """Reconcile ``blueprint`` into ``db`` in one transaction; return the diff."""
    desired_users = _desired_users(blueprint)
    desired_teams = _desired_teams(blueprint)
    desired_pipelines = _desired_pipelines(blueprint)
    desired_members = _desired_members(blueprint)
    desired_permissions = _desired_permissions(blueprint)

    diff = BlueprintDiff()
    async with db.transaction() as tx:
        current_users = {row.id: row for row in await tx.fetch_all(select(User).all())}
        current_teams = {row.id: row for row in await tx.fetch_all(select(Team).all())}
        current_pipelines = {
            row.id: row for row in await tx.fetch_all(select(Pipeline).all())
        }
        current_members = {
            (row.team_id, row.user_id): row
            for row in await tx.fetch_all(select(TeamMember).all())
        }
        current_permissions = {
            (row.pipeline_id, row.team_id): row
            for row in await tx.fetch_all(select(PipelinePermission).all())
        }

        member_changed_teams = _changed_member_teams(desired_members, current_members)
        permission_changed_pipelines = _changed_permission_pipelines(
            desired_permissions, current_permissions
        )

        # Deletions first, children before parents.
        await _delete_extra_permissions(tx, desired_permissions, current_permissions)
        await _delete_extra_pipelines(tx, desired_pipelines, current_pipelines, diff)
        await _delete_extra_members(tx, desired_members, current_members)
        await _delete_extra_teams(tx, desired_teams, current_teams, diff)
        await _delete_extra_users(tx, desired_users, current_users, diff)

        # Upserts next, parents before children.
        await _upsert_users(tx, desired_users, current_users, diff)
        await _upsert_teams(tx, desired_teams, current_teams, diff)
        await _upsert_pipelines(tx, desired_pipelines, current_pipelines, diff)
        await _upsert_members(tx, desired_members, current_members)
        await _upsert_permissions(tx, desired_permissions, current_permissions)

        _mark_child_changes(
            diff.teams,
            member_changed_teams,
            {team.id: team.name for team in desired_teams.values()}
            | {row.id: row.name for row in current_teams.values()},
        )
        _mark_child_changes(
            diff.pipelines,
            permission_changed_pipelines,
            {pipeline.id: pipeline.name for pipeline in desired_pipelines.values()}
            | {row.id: row.name for row in current_pipelines.values()},
        )
    return diff


# --- desired-state builders -------------------------------------------------


def _desired_users(blueprint: Blueprint) -> dict[str, _DesiredUser]:
    desired: dict[str, _DesiredUser] = {}
    for user in blueprint.users:
        user_id = _entity_id("user", user.spec.email)
        desired[user_id] = _DesiredUser(
            id=user_id, email=user.spec.email, name=user.metadata.name
        )
    return desired


def _desired_teams(blueprint: Blueprint) -> dict[str, _DesiredTeam]:
    desired: dict[str, _DesiredTeam] = {}
    for team in blueprint.teams:
        team_id = _entity_id("team", team.metadata.name)
        desired[team_id] = _DesiredTeam(
            id=team_id, name=team.metadata.name, global_admin=team.spec.global_admin
        )
    return desired


def _desired_pipelines(blueprint: Blueprint) -> dict[str, _DesiredPipeline]:
    desired: dict[str, _DesiredPipeline] = {}
    for pipeline in blueprint.pipelines:
        pipeline_id = _entity_id("pipeline", pipeline.metadata.name)
        branch_filter = (
            json.dumps(pipeline.spec.branch_filter)
            if pipeline.spec.branch_filter
            else None
        )
        cron_schedule = next(
            (
                trigger.schedule
                for trigger in pipeline.spec.triggers
                if trigger.on == "cron" and trigger.schedule is not None
            ),
            None,
        )
        desired[pipeline_id] = _DesiredPipeline(
            id=pipeline_id,
            name=pipeline.metadata.name,
            team_id=_entity_id("team", pipeline.metadata.team),
            repo_url=pipeline.spec.repository,
            branch_filter=branch_filter,
            cron_schedule=cron_schedule,
            config_path=pipeline.spec.script,
            worker_image=pipeline.spec.worker.image,
            max_duration_seconds=pipeline.spec.max_duration_seconds,
            workspace_size_gb=pipeline.spec.workspace_size_gb,
            egress=pipeline.spec.egress,
        )
    return desired


def _desired_members(blueprint: Blueprint) -> dict[tuple[str, str], str]:
    desired: dict[tuple[str, str], str] = {}
    for team in blueprint.teams:
        team_id = _entity_id("team", team.metadata.name)
        for email in team.spec.members:
            desired[(team_id, _entity_id("user", email))] = _MEMBER_ROLE
    return desired


def _desired_permissions(blueprint: Blueprint) -> dict[tuple[str, str], str]:
    desired: dict[tuple[str, str], str] = {}
    for pipeline in blueprint.pipelines:
        pipeline_id = _entity_id("pipeline", pipeline.metadata.name)
        for permission in pipeline.spec.permissions:
            team_id = _entity_id("team", permission.team)
            desired[(pipeline_id, team_id)] = permission.level.value
    return desired


# --- reconcile helpers ------------------------------------------------------


async def _delete_extra_users(
    tx: Transaction,
    desired: dict[str, _DesiredUser],
    current: dict[str, User[Fetched]],
    diff: BlueprintDiff,
) -> None:
    for user_id, row in current.items():
        if user_id not in desired:
            _ = await tx.execute(delete(User).where(User.id.eq(user_id)))
            diff.users.removed.append(row.name)


async def _upsert_users(
    tx: Transaction,
    desired: dict[str, _DesiredUser],
    current: dict[str, User[Fetched]],
    diff: BlueprintDiff,
) -> None:
    for user_id, want in desired.items():
        existing = current.get(user_id)
        if existing is None:
            await tx.execute(insert(User(id=want.id, email=want.email, name=want.name)))
            diff.users.added.append(want.name)
        elif existing.name != want.name:
            # email is the identity, so only the display name can drift here;
            # leave oidc_subject untouched so an OIDC link survives a re-sync.
            _ = await tx.execute(
                update(User)
                .set(User.name.to(want.name))
                .set(User.updated_at.to(CurrentTimestamp))
                .where(User.id.eq(user_id))
            )
            diff.users.updated.append(want.name)


async def _delete_extra_teams(
    tx: Transaction,
    desired: dict[str, _DesiredTeam],
    current: dict[str, Team[Fetched]],
    diff: BlueprintDiff,
) -> None:
    for team_id, row in current.items():
        if team_id not in desired:
            _ = await tx.execute(delete(Team).where(Team.id.eq(team_id)))
            diff.teams.removed.append(row.name)


async def _upsert_teams(
    tx: Transaction,
    desired: dict[str, _DesiredTeam],
    current: dict[str, Team[Fetched]],
    diff: BlueprintDiff,
) -> None:
    for team_id, want in desired.items():
        existing = current.get(team_id)
        if existing is None:
            await tx.execute(
                insert(Team(id=want.id, name=want.name, global_admin=want.global_admin))
            )
            diff.teams.added.append(want.name)
        elif existing.global_admin != want.global_admin:
            _ = await tx.execute(
                update(Team)
                .set(Team.global_admin.to(want.global_admin))
                .where(Team.id.eq(team_id))
            )
            diff.teams.updated.append(want.name)


async def _delete_extra_pipelines(
    tx: Transaction,
    desired: dict[str, _DesiredPipeline],
    current: dict[str, Pipeline[Fetched]],
    diff: BlueprintDiff,
) -> None:
    for pipeline_id, row in current.items():
        if pipeline_id not in desired:
            _ = await tx.execute(delete(Pipeline).where(Pipeline.id.eq(pipeline_id)))
            diff.pipelines.removed.append(row.name)


async def _upsert_pipelines(
    tx: Transaction,
    desired: dict[str, _DesiredPipeline],
    current: dict[str, Pipeline[Fetched]],
    diff: BlueprintDiff,
) -> None:
    for pipeline_id, want in desired.items():
        existing = current.get(pipeline_id)
        if existing is None:
            await tx.execute(
                insert(
                    Pipeline(
                        id=want.id,
                        name=want.name,
                        team_id=want.team_id,
                        repo_url=want.repo_url,
                        branch_filter=want.branch_filter,
                        cron_schedule=want.cron_schedule,
                        config_path=want.config_path,
                        worker_image=want.worker_image,
                        max_duration_seconds=want.max_duration_seconds,
                        workspace_size_gb=want.workspace_size_gb,
                        egress=want.egress,
                    )
                )
            )
            diff.pipelines.added.append(want.name)
        elif _pipeline_differs(existing, want):
            _ = await tx.execute(
                update(Pipeline)
                .set(Pipeline.team_id.to(want.team_id))
                .set(Pipeline.repo_url.to(want.repo_url))
                .set(Pipeline.branch_filter.to(want.branch_filter))
                .set(Pipeline.cron_schedule.to(want.cron_schedule))
                .set(Pipeline.config_path.to(want.config_path))
                .set(Pipeline.worker_image.to(want.worker_image))
                .set(Pipeline.max_duration_seconds.to(want.max_duration_seconds))
                .set(Pipeline.workspace_size_gb.to(want.workspace_size_gb))
                .set(Pipeline.egress.to(want.egress))
                .set(Pipeline.updated_at.to(CurrentTimestamp))
                .where(Pipeline.id.eq(pipeline_id))
            )
            diff.pipelines.updated.append(want.name)


def _pipeline_differs(existing: Pipeline[Fetched], want: _DesiredPipeline) -> bool:
    return (
        existing.team_id != want.team_id
        or existing.repo_url != want.repo_url
        or existing.branch_filter != want.branch_filter
        or existing.cron_schedule != want.cron_schedule
        or existing.config_path != want.config_path
        or existing.worker_image != want.worker_image
        or existing.max_duration_seconds != want.max_duration_seconds
        or existing.workspace_size_gb != want.workspace_size_gb
        or existing.egress != want.egress
    )


async def _delete_extra_members(
    tx: Transaction,
    desired: dict[tuple[str, str], str],
    current: dict[tuple[str, str], TeamMember[Fetched]],
) -> None:
    for team_id, user_id in current:
        if (team_id, user_id) not in desired:
            _ = await tx.execute(
                delete(TeamMember).where(
                    TeamMember.team_id.eq(team_id) & TeamMember.user_id.eq(user_id)
                )
            )


async def _upsert_members(
    tx: Transaction,
    desired: dict[tuple[str, str], str],
    current: dict[tuple[str, str], TeamMember[Fetched]],
) -> None:
    for (team_id, user_id), role in desired.items():
        existing = current.get((team_id, user_id))
        if existing is None:
            await tx.execute(
                insert(TeamMember(team_id=team_id, user_id=user_id, role=role))
            )
        elif existing.role != role:
            _ = await tx.execute(
                update(TeamMember)
                .set(TeamMember.role.to(role))
                .where(TeamMember.team_id.eq(team_id) & TeamMember.user_id.eq(user_id))
            )


async def _delete_extra_permissions(
    tx: Transaction,
    desired: dict[tuple[str, str], str],
    current: dict[tuple[str, str], PipelinePermission[Fetched]],
) -> None:
    for pipeline_id, team_id in current:
        if (pipeline_id, team_id) not in desired:
            _ = await tx.execute(
                delete(PipelinePermission).where(
                    PipelinePermission.pipeline_id.eq(pipeline_id)
                    & PipelinePermission.team_id.eq(team_id)
                )
            )


async def _upsert_permissions(
    tx: Transaction,
    desired: dict[tuple[str, str], str],
    current: dict[tuple[str, str], PipelinePermission[Fetched]],
) -> None:
    for (pipeline_id, team_id), level in desired.items():
        existing = current.get((pipeline_id, team_id))
        if existing is None:
            await tx.execute(
                insert(
                    PipelinePermission(
                        pipeline_id=pipeline_id, team_id=team_id, level=level
                    )
                )
            )
        elif existing.level != level:
            _ = await tx.execute(
                update(PipelinePermission)
                .set(PipelinePermission.level.to(level))
                .where(
                    PipelinePermission.pipeline_id.eq(pipeline_id)
                    & PipelinePermission.team_id.eq(team_id)
                )
            )


def _changed_member_teams(
    desired: dict[tuple[str, str], str],
    current: dict[tuple[str, str], TeamMember[Fetched]],
) -> set[str]:
    changed: set[str] = set()
    for key in desired.keys() ^ current.keys():
        changed.add(key[0])
    for key in desired.keys() & current.keys():
        if desired[key] != current[key].role:
            changed.add(key[0])
    return changed


def _changed_permission_pipelines(
    desired: dict[tuple[str, str], str],
    current: dict[tuple[str, str], PipelinePermission[Fetched]],
) -> set[str]:
    changed: set[str] = set()
    for key in desired.keys() ^ current.keys():
        changed.add(key[0])
    for key in desired.keys() & current.keys():
        if desired[key] != current[key].level:
            changed.add(key[0])
    return changed


def _mark_child_changes(
    change: EntityChange, parent_ids: set[str], names: dict[str, str]
) -> None:
    """Record parents whose child rows changed as ``updated`` in the diff.

    A parent already counted as added or removed is left alone -- its child
    churn is implied by the add/remove and would otherwise be double counted.
    """
    accounted = set(change.added) | set(change.removed) | set(change.updated)
    for parent_id in parent_ids:
        name = names.get(parent_id)
        if name is not None and name not in accounted:
            change.updated.append(name)
            accounted.add(name)
