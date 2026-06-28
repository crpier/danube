"""Pydantic response models for the read-only HTTP API.

These mirror the persisted rows in `danube.db.models` but are decoupled from the
snekql layer: they are the stable wire contract for API clients. `from_attributes`
lets a route build a response straight from a fetched snekql row.
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from danube.domain.enums import JobStatus, TriggerType


class JobResponse(BaseModel):
    """A single job row as returned by the jobs endpoints."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    pipeline_id: str
    trigger_type: TriggerType
    trigger_ref: str | None
    status: JobStatus
    runner_id: str | None
    workspace_path: str | None
    started_at: datetime | None
    finished_at: datetime | None
    log_path: str | None
    failure_reason: str | None
    created_at: datetime


class PipelineResponse(BaseModel):
    """A single pipeline row as returned by the pipelines endpoints."""

    model_config = ConfigDict(from_attributes=True)

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
    created_at: datetime
    updated_at: datetime


class RunPipelineRequest(BaseModel):
    """Body for a manual pipeline trigger.

    `ref` is the optional git ref (branch/tag/sha) to build; when omitted the
    pipeline's default is used. The body itself is optional on the request.
    """

    ref: str | None = None


class Page[T](BaseModel):
    """A paginated slice of a larger result set.

    `total` is the full count ignoring `limit`/`offset`, so clients can tell when
    more pages remain.
    """

    items: list[T]
    total: int
    limit: int
    offset: int


class HealthResponse(BaseModel):
    """Liveness payload: the process is up and serving requests."""

    status: str = "ok"


class ReadyResponse(BaseModel):
    """Readiness payload: whether dependencies (the database) are reachable."""

    status: str
    database: bool
