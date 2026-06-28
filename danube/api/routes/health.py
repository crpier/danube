"""Liveness and readiness endpoints.

`/health` answers whether the process is up; `/health/ready` additionally probes
the database so a load balancer only routes traffic once dependencies are usable.
A failed readiness probe returns 503 with `database=false` rather than raising.
"""

import logging

from fastapi import APIRouter, Response, status
from snekql.sqlite import select

from danube.api.deps import DbDep
from danube.api.schemas import HealthResponse, ReadyResponse
from danube.db.models import Job

logger = logging.getLogger("danube.api.health")

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> HealthResponse:
    return HealthResponse()


@router.get("/health/ready")
async def ready(db: DbDep, response: Response) -> ReadyResponse:
    database_ok = await _database_reachable(db)
    if not database_ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadyResponse(
        status="ready" if database_ok else "unavailable", database=database_ok
    )


async def _database_reachable(db: DbDep) -> bool:
    """Run a trivial query to confirm the database answers."""
    try:
        async with db.transaction() as tx:
            _ = await tx.fetch_one(select(Job.id.count()).all())
    except Exception:
        logger.exception("readiness database probe failed")
        return False
    return True
