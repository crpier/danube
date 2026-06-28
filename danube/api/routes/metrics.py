"""Prometheus metrics endpoint.

`GET /metrics` renders the injected `Metrics` registry in the Prometheus text
exposition format (`docs/architecture/observability.md`). The registry is the same
one the orchestrator records into, so the exposition reflects live job/runner
counters.
"""

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

from danube.api.deps import MetricsDep
from danube.observability import CONTENT_TYPE

router = APIRouter(tags=["metrics"])


@router.get("/metrics")
async def metrics(registry: MetricsDep) -> PlainTextResponse:
    return PlainTextResponse(content=registry.render(), media_type=CONTENT_TYPE)
