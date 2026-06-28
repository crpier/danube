"""Map verified JWT claims onto Blueprint users/teams and enforce RBAC.

`resolve_principal` turns the `Claims` of a verified token into a `Principal`:
the Blueprint user it identifies, the teams that user belongs to, and whether any
of those teams is `global_admin`. A principal is matched first by OIDC subject
(`users.oidc_subject`) and then by email (`users.email`), so a Blueprint user is
reachable whether the IdP keys on a stable subject or on email.

The permission helpers implement the team-based RBAC model from
`docs/architecture/security.md`: teams hold a level (`read` < `write` < `admin`)
on a pipeline, a `global_admin` team has full access, and an action requires a
minimum level. The functions answer "may this principal do X to this pipeline"
and "which pipelines may this principal read"; the HTTP layer turns a `False`
into a 403.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from snekql.sqlite import NoResultError, select

from danube.db.models import PipelinePermission, Team, TeamMember, User
from danube.domain.enums import PermissionLevel

if TYPE_CHECKING:
    from snekql.sqlite import Database, Fetched, Transaction

    from danube.auth.jwt import Claims

# read < write < admin: a higher rank satisfies every requirement at or below it.
_LEVEL_RANK: dict[PermissionLevel, int] = {
    PermissionLevel.READ: 1,
    PermissionLevel.WRITE: 2,
    PermissionLevel.ADMIN: 3,
}


@dataclass(frozen=True, slots=True)
class Principal:
    """An authenticated UI/API caller resolved to Blueprint identity and teams.

    `user_id` is empty when the token verified but maps to no Blueprint user; such
    a principal holds no teams and is not an admin, so every RBAC check denies it.
    """

    user_id: str
    email: str
    is_global_admin: bool
    team_ids: frozenset[str]


async def resolve_principal(db: Database, claims: Claims) -> Principal | None:
    """Resolve verified ``claims`` to a `Principal`, or `None` if no user matches."""
    async with db.transaction() as tx:
        user = await _find_user(tx, claims)
        if user is None:
            return None
        member_rows = await tx.fetch_all(
            select(TeamMember.team_id).where(TeamMember.user_id.eq(user.id))
        )
        team_ids = frozenset(member_rows)
        is_admin = await _has_global_admin(tx, team_ids)
    return Principal(
        user_id=user.id,
        email=user.email,
        is_global_admin=is_admin,
        team_ids=team_ids,
    )


async def _find_user(tx: Transaction, claims: Claims) -> User[Fetched] | None:
    if claims.subject:
        try:
            return await tx.fetch_one(
                select(User).where(User.oidc_subject.eq(claims.subject))
            )
        except NoResultError:
            pass
    if claims.email:
        try:
            return await tx.fetch_one(select(User).where(User.email.eq(claims.email)))
        except NoResultError:
            return None
    return None


async def _has_global_admin(tx: Transaction, team_ids: frozenset[str]) -> bool:
    if not team_ids:
        return False
    rows = await tx.fetch_all(
        select(Team.id).where(
            Team.id.in_(*team_ids) & Team.global_admin.eq(True)  # noqa: FBT003
        )
    )
    return len(rows) > 0


async def pipeline_permission_ok(
    db: Database,
    principal: Principal,
    pipeline_id: str,
    required: PermissionLevel,
) -> bool:
    """Whether ``principal`` holds at least ``required`` on ``pipeline_id``."""
    if principal.is_global_admin:
        return True
    if not principal.team_ids:
        return False
    async with db.transaction() as tx:
        rows = await tx.fetch_all(
            select(PipelinePermission.level).where(
                PipelinePermission.pipeline_id.eq(pipeline_id)
                & PipelinePermission.team_id.in_(*principal.team_ids)
            )
        )
    needed = _LEVEL_RANK[required]
    return any(_LEVEL_RANK.get(PermissionLevel(level), 0) >= needed for level in rows)


async def readable_pipeline_ids(db: Database, principal: Principal) -> set[str]:
    """The pipeline ids ``principal`` may read (any held level grants read).

    Only meaningful for non-admin principals; a `global_admin` reads everything,
    so callers should short-circuit before calling this.
    """
    if not principal.team_ids:
        return set()
    async with db.transaction() as tx:
        rows = await tx.fetch_all(
            select(PipelinePermission.pipeline_id).where(
                PipelinePermission.team_id.in_(*principal.team_ids)
            )
        )
    return set(rows)
