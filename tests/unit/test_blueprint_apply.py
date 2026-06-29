"""Unit tests for transactional Blueprint application into SQLite.

These build a `Blueprint` directly from spec documents, apply it to an in-memory
database, and assert the rows landed, that a re-apply is a no-op, that a single
changed entity is the only thing touched on the next apply, and that removals
delete the entity. The diff returned by `apply_blueprint` is checked alongside the
row state so the "applies only changed entities" guarantee is pinned.
"""

from collections.abc import AsyncGenerator

from snekql.sqlite import Database, select
from snektest import assert_eq, fixture, load_fixture, test

from danube.blueprint import Blueprint, apply_blueprint
from danube.blueprint.spec import (
    PermissionSpec,
    PipelineDocument,
    PipelineLimits,
    PipelineMetadata,
    PipelineSpec,
    PipelineTrigger,
    TeamDocument,
    TeamSpec,
    UserDocument,
    UserSpec,
    WorkerSpec,
    _NamedMetadata,
)
from danube.db import open_database
from danube.db.models import Pipeline, PipelinePermission, Team, TeamMember, User
from danube.domain.enums import PermissionLevel

API = "danube.dev/v1"


def _user(name: str, email: str) -> UserDocument:
    return UserDocument(
        apiVersion=API,
        kind="User",
        metadata=_NamedMetadata(name=name),
        spec=UserSpec(email=email),
    )


def _team(name: str, members: list[str], *, global_admin: bool = False) -> TeamDocument:
    return TeamDocument(
        apiVersion=API,
        kind="Team",
        metadata=_NamedMetadata(name=name),
        spec=TeamSpec(members=members, global_admin=global_admin),
    )


def _pipeline(
    name: str,
    team: str,
    *,
    image: str = "node:20-alpine",
    cron: str | None = None,
    egress: bool = False,
    limits: PipelineLimits | None = None,
    permissions: list[PermissionSpec] | None = None,
) -> PipelineDocument:
    triggers = [PipelineTrigger(on="cron", schedule=cron)] if cron is not None else []
    return PipelineDocument(
        apiVersion=API,
        kind="Pipeline",
        metadata=PipelineMetadata(name=name, team=team),
        spec=PipelineSpec(
            repository=f"https://example.test/{name}.git",
            worker=WorkerSpec(image=image),
            triggers=triggers,
            egress=egress,
            limits=limits or PipelineLimits(),
            permissions=permissions or [],
        ),
    )


def _blueprint(
    *,
    users: list[UserDocument] | None = None,
    teams: list[TeamDocument] | None = None,
    pipelines: list[PipelineDocument] | None = None,
) -> Blueprint:
    return Blueprint(
        config=None,
        users=users or [],
        teams=teams or [],
        pipelines=pipelines or [],
    )


@fixture
async def db() -> AsyncGenerator[Database]:
    database = await open_database(":memory:")
    try:
        yield database
    finally:
        await database.close()


@test(mark="fast")
async def test_apply_inserts_users_teams_and_pipelines() -> None:
    database = await load_fixture(db())
    blueprint = _blueprint(
        users=[_user("alice", "alice@example.com")],
        teams=[_team("engineering", ["alice@example.com"], global_admin=True)],
        pipelines=[
            _pipeline(
                "frontend",
                "engineering",
                permissions=[
                    PermissionSpec(team="engineering", level=PermissionLevel.ADMIN)
                ],
            )
        ],
    )

    diff = await apply_blueprint(database, blueprint)

    async with database.transaction() as tx:
        users = await tx.fetch_all(select(User).all())
        teams = await tx.fetch_all(select(Team).all())
        members = await tx.fetch_all(select(TeamMember).all())
        pipelines = await tx.fetch_all(select(Pipeline).all())
        permissions = await tx.fetch_all(select(PipelinePermission).all())

    assert_eq(len(users), 1)
    assert_eq(users[0].email, "alice@example.com")
    assert_eq(len(teams), 1)
    assert_eq(teams[0].global_admin, True)
    assert_eq(len(members), 1)
    assert_eq(len(pipelines), 1)
    assert_eq(pipelines[0].team_id, teams[0].id)
    assert_eq(len(permissions), 1)
    assert_eq(diff.applied_counts(), {"user": 1, "team": 1, "pipeline": 1})


@test(mark="fast")
async def test_reapplying_identical_blueprint_is_a_noop() -> None:
    database = await load_fixture(db())
    blueprint = _blueprint(
        users=[_user("alice", "alice@example.com")],
        teams=[_team("engineering", ["alice@example.com"])],
        pipelines=[_pipeline("frontend", "engineering")],
    )
    _ = await apply_blueprint(database, blueprint)

    diff = await apply_blueprint(database, blueprint)

    assert diff.is_empty
    assert_eq(diff.applied_counts(), {"user": 0, "team": 0, "pipeline": 0})


@test(mark="fast")
async def test_only_the_changed_pipeline_is_updated() -> None:
    database = await load_fixture(db())
    teams = [_team("engineering", [])]
    base = _blueprint(
        teams=teams,
        pipelines=[
            _pipeline("frontend", "engineering"),
            _pipeline("backend", "engineering"),
        ],
    )
    _ = await apply_blueprint(database, base)

    changed = _blueprint(
        teams=teams,
        pipelines=[
            _pipeline("frontend", "engineering", image="node:22-alpine"),
            _pipeline("backend", "engineering"),
        ],
    )
    diff = await apply_blueprint(database, changed)

    assert_eq(diff.pipelines.updated, ["frontend"])
    assert_eq(diff.pipelines.added, [])
    assert_eq(diff.pipelines.removed, [])
    async with database.transaction() as tx:
        frontend = await tx.fetch_one(
            select(Pipeline).where(Pipeline.name.eq("frontend"))
        )
    assert_eq(frontend.worker_image, "node:22-alpine")


@test(mark="fast")
async def test_pipeline_defaults_to_egress_denied() -> None:
    database = await load_fixture(db())
    blueprint = _blueprint(
        teams=[_team("engineering", [])],
        pipelines=[_pipeline("frontend", "engineering")],
    )

    _ = await apply_blueprint(database, blueprint)

    async with database.transaction() as tx:
        frontend = await tx.fetch_one(
            select(Pipeline).where(Pipeline.name.eq("frontend"))
        )
    assert_eq(frontend.egress, False)


@test(mark="fast")
async def test_changing_egress_updates_the_pipeline() -> None:
    database = await load_fixture(db())
    teams = [_team("engineering", [])]
    _ = await apply_blueprint(
        database,
        _blueprint(teams=teams, pipelines=[_pipeline("frontend", "engineering")]),
    )

    diff = await apply_blueprint(
        database,
        _blueprint(
            teams=teams,
            pipelines=[_pipeline("frontend", "engineering", egress=True)],
        ),
    )

    assert_eq(diff.pipelines.updated, ["frontend"])
    async with database.transaction() as tx:
        frontend = await tx.fetch_one(
            select(Pipeline).where(Pipeline.name.eq("frontend"))
        )
    assert_eq(frontend.egress, True)


@test(mark="fast")
async def test_pipeline_limits_default_to_null() -> None:
    database = await load_fixture(db())
    blueprint = _blueprint(
        teams=[_team("engineering", [])],
        pipelines=[_pipeline("frontend", "engineering")],
    )

    _ = await apply_blueprint(database, blueprint)

    async with database.transaction() as tx:
        frontend = await tx.fetch_one(
            select(Pipeline).where(Pipeline.name.eq("frontend"))
        )
    assert_eq(frontend.limit_cpu, None)
    assert_eq(frontend.limit_memory_mb, None)
    assert_eq(frontend.limit_pids, None)


@test(mark="fast")
async def test_requested_limits_are_persisted_and_updated() -> None:
    database = await load_fixture(db())
    teams = [_team("engineering", [])]
    _ = await apply_blueprint(
        database,
        _blueprint(
            teams=teams,
            pipelines=[
                _pipeline(
                    "frontend",
                    "engineering",
                    limits=PipelineLimits(cpu=1.5, memory_mb=1024, pids=256),
                )
            ],
        ),
    )

    async with database.transaction() as tx:
        frontend = await tx.fetch_one(
            select(Pipeline).where(Pipeline.name.eq("frontend"))
        )
    assert_eq(frontend.limit_cpu, 1.5)
    assert_eq(frontend.limit_memory_mb, 1024)
    assert_eq(frontend.limit_pids, 256)

    diff = await apply_blueprint(
        database,
        _blueprint(
            teams=teams,
            pipelines=[
                _pipeline(
                    "frontend",
                    "engineering",
                    limits=PipelineLimits(cpu=3.0, memory_mb=1024, pids=256),
                )
            ],
        ),
    )

    assert_eq(diff.pipelines.updated, ["frontend"])
    async with database.transaction() as tx:
        frontend = await tx.fetch_one(
            select(Pipeline).where(Pipeline.name.eq("frontend"))
        )
    assert_eq(frontend.limit_cpu, 3.0)


@test(mark="fast")
async def test_changed_membership_marks_team_updated() -> None:
    database = await load_fixture(db())
    _ = await apply_blueprint(
        database,
        _blueprint(
            users=[_user("alice", "alice@example.com")],
            teams=[_team("engineering", [])],
        ),
    )

    diff = await apply_blueprint(
        database,
        _blueprint(
            users=[_user("alice", "alice@example.com")],
            teams=[_team("engineering", ["alice@example.com"])],
        ),
    )

    assert_eq(diff.teams.updated, ["engineering"])
    async with database.transaction() as tx:
        members = await tx.fetch_all(select(TeamMember).all())
    assert_eq(len(members), 1)


@test(mark="fast")
async def test_removed_entities_are_deleted() -> None:
    database = await load_fixture(db())
    _ = await apply_blueprint(
        database,
        _blueprint(
            teams=[_team("engineering", []), _team("platform", [])],
            pipelines=[_pipeline("frontend", "engineering")],
        ),
    )

    diff = await apply_blueprint(database, _blueprint(teams=[_team("engineering", [])]))

    assert_eq(diff.teams.removed, ["platform"])
    assert_eq(diff.pipelines.removed, ["frontend"])
    async with database.transaction() as tx:
        teams = await tx.fetch_all(select(Team).all())
        pipelines = await tx.fetch_all(select(Pipeline).all())
    assert_eq(len(teams), 1)
    assert_eq(len(pipelines), 0)
