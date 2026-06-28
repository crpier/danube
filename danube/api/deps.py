"""Shared FastAPI dependencies for the read-only API.

The database is injected rather than imported as a global so the app is testable
against an in-memory snekql `Database`: `create_app` registers a
`dependency_overrides` entry for `get_db`, and tests can do the same with their
own database. The bare `get_db` is never executed in practice; it exists only as
the dependency key and fails loudly if wiring is missing.
"""

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Query
from snekql.sqlite import Database

from danube.orchestrator import JobManager

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
