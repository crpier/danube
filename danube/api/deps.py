"""Shared FastAPI dependencies for the read-only API.

The database is injected rather than imported as a global so the app is testable
against an in-memory snekql `Database`: `create_app` registers a
`dependency_overrides` entry for `get_db`, and tests can do the same with their
own database. The bare `get_db` is never executed in practice; it exists only as
the dependency key and fails loudly if wiring is missing.
"""

import logging
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Query, status
from snekql.sqlite import Database, NoResultError, select

from danube.auth import (
    AuthConfig,
    JwtError,
    Principal,
    pipeline_permission_ok,
    resolve_principal,
    verify,
)
from danube.db.models import Job
from danube.domain.enums import PermissionLevel
from danube.observability import Metrics, Tracer
from danube.orchestrator import JobManager
from danube.runner.base import Runner
from danube.webhooks import WebhookConfig

_audit_logger = logging.getLogger("danube.auth")

# Bound the page size so a client cannot ask for an unbounded result set.
MAX_PAGE_LIMIT = 200
DEFAULT_PAGE_LIMIT = 50


def get_db() -> Database:
    """Dependency key for the request-scoped database.

    Always overridden via `app.dependency_overrides` in `create_app`; reaching
    this body means the app was constructed without a database.
    """
    msg = "database dependency is not configured; build the app with create_app"
    raise RuntimeError(msg)


DbDep = Annotated[Database, Depends(get_db)]


def get_job_manager() -> JobManager:
    """Dependency key for the request-scoped job manager.

    Always overridden via `app.dependency_overrides` in `create_app` when a job
    manager is supplied; reaching this body means a control-plane route was
    mounted without one.
    """
    msg = "job manager dependency is not configured; build the app with create_app"
    raise RuntimeError(msg)


JobManagerDep = Annotated[JobManager, Depends(get_job_manager)]


def get_webhook_config() -> WebhookConfig:
    """Dependency key for the per-provider webhook secrets.

    Always overridden via `app.dependency_overrides` in `create_app` when webhook
    ingestion is mounted; reaching this body means a webhook route was mounted
    without a configuration.
    """
    msg = "webhook config dependency is not configured; build the app with create_app"
    raise RuntimeError(msg)


WebhookConfigDep = Annotated[WebhookConfig, Depends(get_webhook_config)]


def get_metrics() -> Metrics:
    """Dependency key for the process-wide metrics registry.

    Always overridden via `app.dependency_overrides` in `create_app`; reaching
    this body means the app was constructed without a metrics registry.
    """
    msg = "metrics dependency is not configured; build the app with create_app"
    raise RuntimeError(msg)


MetricsDep = Annotated[Metrics, Depends(get_metrics)]


def get_tracer() -> Tracer:
    """Dependency key for the request tracer.

    Always overridden via `app.dependency_overrides` in `create_app`; reaching
    this body means the app was constructed without a tracer.
    """
    msg = "tracer dependency is not configured; build the app with create_app"
    raise RuntimeError(msg)


TracerDep = Annotated[Tracer, Depends(get_tracer)]


def get_runner() -> Runner | None:
    """Dependency key for the optional readiness runner.

    Returns `None` unless `create_app` was given a runner; the readiness probe
    then limits itself to the database check.
    """
    return None


RunnerDep = Annotated["Runner | None", Depends(get_runner)]


@dataclass(frozen=True, slots=True)
class PageParams:
    """Validated `limit`/`offset` pagination parameters."""

    limit: int
    offset: int


def page_params(
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_LIMIT)] = DEFAULT_PAGE_LIMIT,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PageParams:
    return PageParams(limit=limit, offset=offset)


PageDep = Annotated[PageParams, Depends(page_params)]


# --- authentication & authorization -----------------------------------------


def get_auth_config() -> AuthConfig | None:
    """Dependency key for the optional UI/API auth configuration.

    Returns `None` (auth disabled) unless `create_app` was given an
    `AuthConfig`, in which case the override returns it. A `None` config makes
    every auth dependency a no-op, which keeps the read-only/in-process test apps
    unauthenticated.
    """
    return None


AuthConfigDep = Annotated["AuthConfig | None", Depends(get_auth_config)]


def _bearer_token(authorization: str | None) -> str:
    prefix = "Bearer "
    if authorization is None or not authorization.startswith(prefix):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing or malformed bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return authorization.removeprefix(prefix)


async def current_principal(
    auth_config: AuthConfigDep,
    db: DbDep,
    authorization: Annotated[str | None, Header()] = None,
) -> Principal | None:
    """Resolve the caller's `Principal`, or `None` when auth is disabled.

    With auth enabled: a missing/malformed token or a token that fails JWT
    validation (signature, expiry, issuer, audience) is a 401. A valid token that
    maps to no Blueprint user yields a zero-permission principal (so RBAC denies
    it with a 403) rather than a 401 -- the caller authenticated, they just are
    not authorized.
    """
    if auth_config is None:
        return None
    token = _bearer_token(authorization)
    try:
        claims = verify(token, auth_config)
    except JwtError as error:
        _audit_logger.info(
            "auth_rejected",
            extra={"event": "auth_rejected", "reason": str(error)},
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid authentication token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from None
    principal = await resolve_principal(db, claims)
    if principal is None:
        return Principal(
            user_id="",
            email=claims.email or claims.subject,
            is_global_admin=False,
            team_ids=frozenset(),
        )
    return principal


PrincipalDep = Annotated["Principal | None", Depends(current_principal)]


def _deny(principal: Principal, resource: str, required: PermissionLevel) -> None:
    _audit_logger.info(
        "auth_permission_denied",
        extra={
            "event": "auth_permission_denied",
            "user": principal.email or principal.user_id,
            "resource": resource,
            "required_level": str(required),
        },
    )
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="insufficient permission for this resource",
    )


async def _enforce_pipeline(
    db: Database,
    principal: Principal | None,
    pipeline_id: str,
    required: PermissionLevel,
) -> None:
    if principal is None:
        return
    if not await pipeline_permission_ok(db, principal, pipeline_id, required):
        _deny(principal, f"pipeline:{pipeline_id}", required)


async def _job_pipeline_id(db: Database, job_id: str) -> str | None:
    async with db.transaction() as tx:
        try:
            return await tx.fetch_one(select(Job.pipeline_id).where(Job.id.eq(job_id)))
        except NoResultError:
            return None


async def _enforce_job(
    db: Database,
    principal: Principal | None,
    job_id: str,
    required: PermissionLevel,
) -> None:
    if principal is None:
        return
    pipeline_id = await _job_pipeline_id(db, job_id)
    # An unknown job is left to the route, which answers 404; not leaking that a
    # job exists is already covered because no permission row can match it.
    if pipeline_id is None:
        return
    if not await pipeline_permission_ok(db, principal, pipeline_id, required):
        _deny(principal, f"job:{job_id}", required)


async def require_pipeline_read(
    pipeline_id: str, principal: PrincipalDep, db: DbDep
) -> None:
    await _enforce_pipeline(db, principal, pipeline_id, PermissionLevel.READ)


async def require_pipeline_write(
    pipeline_id: str, principal: PrincipalDep, db: DbDep
) -> None:
    await _enforce_pipeline(db, principal, pipeline_id, PermissionLevel.WRITE)


async def require_job_read(job_id: str, principal: PrincipalDep, db: DbDep) -> None:
    await _enforce_job(db, principal, job_id, PermissionLevel.READ)


async def require_job_write(job_id: str, principal: PrincipalDep, db: DbDep) -> None:
    await _enforce_job(db, principal, job_id, PermissionLevel.WRITE)
