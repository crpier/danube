"""Frozen data-transfer objects exchanged between the Master and a runner.

These mirror the interface shapes in `docs/architecture/local-runner.md`. They
express Danube concepts only (job ids, workspaces, commands) and never leak
runtime details such as Podman. Every model is immutable (`frozen=True`) and
rejects unknown fields (`extra="forbid"`) so malformed input fails loudly.
"""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

NonEmptyStr = Annotated[str, Field(min_length=1)]


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class StartJobRequest(_Frozen):
    """Asks the runner to create a job environment for a pipeline run."""

    job_id: NonEmptyStr
    pipeline_id: NonEmptyStr
    worker_image: NonEmptyStr
    env: dict[str, str] = Field(default_factory=dict)
    max_duration_seconds: int | None = Field(default=None, gt=0)


class JobHandle(_Frozen):
    """Opaque-to-the-Master reference to a created job environment, returned by
    `start_job` and passed back into later runner calls."""

    job_id: NonEmptyStr
    pod_id: NonEmptyStr
    workspace_path: NonEmptyStr


class ExecStepRequest(_Frozen):
    """A single command to execute in the Worker container."""

    command: NonEmptyStr
    env: dict[str, str] = Field(default_factory=dict)
    timeout_seconds: int | None = Field(default=None, gt=0)


class ExecResult(_Frozen):
    """Outcome of an executed step command."""

    exit_code: int
    stdout: str
    stderr: str


class BuildImageRequest(_Frozen):
    """Asks the runner to build a container image on the host Podman.

    `context_path` is the host-absolute build-context directory (the Master has
    already resolved it inside the job workspace); `dockerfile` is the
    Containerfile path relative to that context. Builds run with networking
    disabled for `RUN` instructions (`docs/adr/0001-host-side-image-build.md`)."""

    tag: NonEmptyStr
    context_path: NonEmptyStr
    dockerfile: NonEmptyStr = "Dockerfile"


class BuildImageResult(_Frozen):
    """Outcome of a host-side Image Build.

    `image_id` is the built image's id/digest, empty when the build failed.
    `output` is the combined build log, streamed to the job log by the Master."""

    success: bool
    image_id: str
    output: str
    tag: NonEmptyStr


class CoordinatorExit(_Frozen):
    """Outcome of the Coordinator process that drove a job's pipeline.

    Returned by `wait_for_coordinator` once the Coordinator exits. `exit_code` is
    the authoritative crash signal: a non-zero code with no terminal
    `report-status` means the pipeline crashed. `stdout`/`stderr` carry the
    Coordinator's own diagnostic output (not step output, which the control plane
    logs separately)."""

    exit_code: int
    stdout: str = ""
    stderr: str = ""


class ReconcileReport(_Frozen):
    """Discrepancies found between DB `runner_state` and live runtime resources,
    one list of affected job ids per category from `local-runner.md`."""

    stale_pods: list[str] = Field(default_factory=list)
    orphaned_containers: list[str] = Field(default_factory=list)
    stale_workspaces: list[str] = Field(default_factory=list)
    missing_pods: list[str] = Field(default_factory=list)
    failed_cleanups: list[str] = Field(default_factory=list)


class RunnerHealth(_Frozen):
    """Runtime health and version snapshot reported by the runner."""

    healthy: bool
    runtime: NonEmptyStr
    version: str | None = None
    detail: str | None = None
