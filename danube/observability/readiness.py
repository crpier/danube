"""Readiness checks for the `/health/ready` probe.

`run_checks` probes the subsystems a Master needs before it can usefully accept
work (`docs/architecture/observability.md`): the database, and — when a runner is
wired — the runner and its container runtime. Each check resolves to `"ok"` or
`"error"`; the caller is ready only when every check is `"ok"`. Checks never
raise: a failing dependency is reported as `"error"` so the probe can answer 503
instead of 500.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from snekql.sqlite import select

from danube.db.models import Job

if TYPE_CHECKING:
    from snekql.sqlite import Database

    from danube.runner.base import Runner

logger = logging.getLogger("danube.api.health")

OK = "ok"
ERROR = "error"


async def _database_ok(db: Database) -> bool:
    try:
        async with db.transaction() as tx:
            _ = await tx.fetch_one(select(Job.id.count()).all())
    except Exception:
        logger.exception("readiness database probe failed")
        return False
    return True


async def run_checks(db: Database, runner: Runner | None) -> dict[str, str]:
    """Run every readiness check and return a name -> status mapping.

    The `database` check is always present. When `runner` is supplied, `runner`
    and `container_runtime` checks are added from the runner's health snapshot:
    `runner` reflects that the runner responded at all, `container_runtime`
    reflects that the underlying runtime reported healthy.
    """
    checks: dict[str, str] = {"database": OK if await _database_ok(db) else ERROR}
    if runner is not None:
        try:
            health = await runner.health()
        except Exception:
            logger.exception("readiness runner probe failed")
            checks["runner"] = ERROR
            checks["container_runtime"] = ERROR
        else:
            checks["runner"] = OK
            checks["container_runtime"] = OK if health.healthy else ERROR
    return checks
