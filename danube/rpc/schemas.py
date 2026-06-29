"""Pydantic request/response models for the Coordinator <-> Master RPC control
plane (`docs/architecture/components.md`, Internal RPC Control Plane).

These are the stable HTTP/JSON wire contract between the SDK in the Coordinator
and the Master. Every request carries `job_id`; the job-scoped bearer token rides
in the `Authorization` header rather than the body so it never lands in a logged
payload. Requests reject unknown fields so a malformed call fails loudly.
"""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from danube.domain.enums import JobStatus

NonEmptyStr = Annotated[str, Field(min_length=1)]


class _Request(BaseModel):
    """Base for RPC request bodies: every call is scoped to one job."""

    model_config = ConfigDict(extra="forbid")

    job_id: NonEmptyStr


class RunStepRequest(_Request):
    """Ask the Master to run one command in the job's Worker container."""

    command: NonEmptyStr
    env: dict[str, str] = Field(default_factory=dict)
    name: str | None = None
    # When false (the default) the Master writes output only to the job log; when
    # true the captured stdout/stderr are also returned in the response.
    capture_output: bool = False


class RunStepResponse(BaseModel):
    """Outcome of a `run-step` call. `stdout`/`stderr` are populated only when the
    request asked to capture output."""

    exit_code: int
    stdout: str | None = None
    stderr: str | None = None


class BuildImageRequest(_Request):
    """Ask the Master to build a container image on the host Podman.

    `context` is a build-context directory relative to the job workspace and
    `dockerfile` is the Containerfile path within it; both default to the common
    case. `tag` is raw and user-controlled (pipelines disambiguate with the Job
    Context, e.g. the commit sha)."""

    tag: NonEmptyStr
    context: NonEmptyStr = "."
    dockerfile: NonEmptyStr = "Dockerfile"
    name: str | None = None


class BuildImageResponse(BaseModel):
    """Outcome of a `build-image` call: the applied tag, the built image's
    id/digest (empty when the build failed), and whether it succeeded."""

    tag: str
    digest: str
    success: bool


class GetSecretRequest(_Request):
    """Fetch a single decrypted secret value the pipeline is authorized for."""

    key: NonEmptyStr


class GetSecretResponse(BaseModel):
    key: str
    value: str


class UploadArtifactRequest(_Request):
    """Register a build artifact produced under the job workspace."""

    name: NonEmptyStr
    # Source path inside the job workspace (e.g. ``dist/app.tar.gz``).
    path: NonEmptyStr
    size_bytes: int = Field(default=0, ge=0)


class UploadArtifactResponse(BaseModel):
    artifact_id: str
    name: str
    size_bytes: int


class ReportStatusRequest(_Request):
    """Report a job-level status transition from the Coordinator."""

    state: JobStatus
    detail: str | None = None


class ReportStatusResponse(BaseModel):
    status: JobStatus
