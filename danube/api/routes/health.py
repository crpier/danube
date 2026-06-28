"""Liveness and readiness endpoints.

`/health` answers whether the process is up; `/health/ready` additionally probes
required subsystems (database, and the runner/container runtime when wired) and
returns the documented `checks` payload. A failed readiness probe returns 503 with
the offending check marked `"error"` rather than raising.
"""

from fastapi import APIRouter, Response, status

from danube.api.deps import DbDep, RunnerDep
from danube.api.schemas import HealthResponse, ReadyResponse
from danube.observability import run_checks
from danube.observability.readiness import OK

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> HealthResponse:
    return HealthResponse()


@router.get("/health/ready")
async def ready(db: DbDep, runner: RunnerDep, response: Response) -> ReadyResponse:
    checks = await run_checks(db, runner)
    ready_now = all(value == OK for value in checks.values())
    if not ready_now:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return ReadyResponse(status="ready" if ready_now else "unavailable", checks=checks)
