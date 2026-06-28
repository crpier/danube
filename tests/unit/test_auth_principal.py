"""Unit tests for principal resolution and team-based RBAC.

Seeds a small users/teams/pipelines graph into an in-memory database and checks
that verified claims map onto the right Blueprint user, that team membership and
`global_admin` are reflected, and that the permission helpers honour the
`read < write < admin` ordering.
"""

from collections.abc import AsyncGenerator

from snekql.sqlite import Database, insert
from snektest import assert_eq, assert_false, assert_true, fixture, load_fixture, test

from danube.auth.jwt import Claims
from danube.auth.principal import (
    pipeline_permission_ok,
    readable_pipeline_ids,
    resolve_principal,
)
from danube.db import open_database
from danube.db.models import (
    Pipeline,
    PipelinePermission,
    Team,
    TeamMember,
    User,
)
from danube.domain.enums import PermissionLevel


def _claims(subject: str = "", email: str | None = None) -> Claims:
    return Claims(subject=subject, email=email, issuer="iss", expires_at=None, raw={})


async def _seed(db: Database) -> None:
    async with db.transaction() as tx:
        await tx.execute(
            insert(
                User(
                    id="u-alice",
                    email="alice@example.com",
                    name="alice",
                    oidc_subject="sub-alice",
                )
            )
        )
        await tx.execute(insert(User(id="u-bob", email="bob@example.com", name="bob")))
        await tx.execute(insert(Team(id="t-eng", name="engineering")))
        await tx.execute(insert(Team(id="t-admin", name="platform", global_admin=True)))
        await tx.execute(insert(TeamMember(team_id="t-eng", user_id="u-alice")))
        await tx.execute(insert(TeamMember(team_id="t-admin", user_id="u-bob")))
        await tx.execute(
            insert(
                Pipeline(
                    id="p-front",
                    name="frontend",
                    team_id="t-eng",
                    repo_url="https://example.test/front.git",
                    worker_image="node:20",
                )
            )
        )
        await tx.execute(
            insert(
                Pipeline(
                    id="p-secret",
                    name="secret",
                    team_id="t-admin",
                    repo_url="https://example.test/secret.git",
                    worker_image="node:20",
                )
            )
        )
        await tx.execute(
            insert(
                PipelinePermission(
                    pipeline_id="p-front", team_id="t-eng", level="write"
                )
            )
        )


@fixture
async def db() -> AsyncGenerator[Database]:
    database = await open_database(":memory:")
    await _seed(database)
    try:
        yield database
    finally:
        await database.close()


@test(mark="medium")
async def test_resolve_by_oidc_subject() -> None:
    database = await load_fixture(db())

    principal = await resolve_principal(database, _claims(subject="sub-alice"))

    assert principal is not None
    assert_eq(principal.user_id, "u-alice")
    assert_eq(principal.team_ids, frozenset({"t-eng"}))
    assert_false(principal.is_global_admin)


@test(mark="medium")
async def test_resolve_by_email_fallback() -> None:
    database = await load_fixture(db())

    principal = await resolve_principal(
        database, _claims(subject="unknown-sub", email="bob@example.com")
    )

    assert principal is not None
    assert_eq(principal.user_id, "u-bob")
    assert_true(principal.is_global_admin)


@test(mark="medium")
async def test_resolve_unknown_principal_is_none() -> None:
    database = await load_fixture(db())

    principal = await resolve_principal(
        database, _claims(subject="nobody", email="nobody@example.com")
    )

    assert_eq(principal, None)


@test(mark="medium")
async def test_write_grants_read_and_write_not_admin() -> None:
    database = await load_fixture(db())
    principal = await resolve_principal(database, _claims(subject="sub-alice"))
    assert principal is not None

    assert_true(
        await pipeline_permission_ok(
            database, principal, "p-front", PermissionLevel.READ
        )
    )
    assert_true(
        await pipeline_permission_ok(
            database, principal, "p-front", PermissionLevel.WRITE
        )
    )
    assert_false(
        await pipeline_permission_ok(
            database, principal, "p-front", PermissionLevel.ADMIN
        )
    )


@test(mark="medium")
async def test_no_permission_on_other_pipeline() -> None:
    database = await load_fixture(db())
    principal = await resolve_principal(database, _claims(subject="sub-alice"))
    assert principal is not None

    assert_false(
        await pipeline_permission_ok(
            database, principal, "p-secret", PermissionLevel.READ
        )
    )


@test(mark="medium")
async def test_global_admin_allowed_everywhere() -> None:
    database = await load_fixture(db())
    principal = await resolve_principal(
        database, _claims(subject="", email="bob@example.com")
    )
    assert principal is not None

    assert_true(
        await pipeline_permission_ok(
            database, principal, "p-front", PermissionLevel.ADMIN
        )
    )
    assert_true(
        await pipeline_permission_ok(
            database, principal, "p-secret", PermissionLevel.ADMIN
        )
    )


@test(mark="medium")
async def test_readable_pipeline_ids_limited_to_permitted() -> None:
    database = await load_fixture(db())
    principal = await resolve_principal(database, _claims(subject="sub-alice"))
    assert principal is not None

    readable = await readable_pipeline_ids(database, principal)

    assert_eq(readable, {"p-front"})
