"""Frozen data-transfer objects exchanged between the Master and a runner.

These mirror the interface shapes in `docs/architecture/local-runner.md`. They
express Danube concepts only (job ids, workspaces, commands) and never leak
runtime details such as Podman. Every model is immutable (`frozen=True`) and
rejects unknown fields (`extra="forbid"`) so malformed input fails loudly.
"""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from danube.domain.limits import ResourceRequest

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
    # Job-level egress posture from the pipeline's Blueprint. Default-deny: the
    # runner attaches the pod to an `internal` (no-egress) network unless this is
    # set, in which case it attaches a normal outbound network instead
    # (`docs/adr/0002-default-deny-egress.md`).
    egress: bool = False
    # Pipeline-requested job resource limits (CPU/memory/pids/timeout). The runner
    # resolves these against its operator-set Resource Ceiling -- absent fields fall
    # back to the ceiling default, and every field is clamped to the ceiling max --
    # before applying the cgroup limits (#53, `docs/configuration/server-config.md`).
    limits: ResourceRequest = Field(default_factory=ResourceRequest)


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
    Containerfile path relative to that context. `build_args` are passed to the
    Dockerfile's `ARG` instructions (not secret-safe). `network` opts `RUN`
    instructions into networking, which is denied by default
    (`docs/adr/0001-host-side-image-build.md`). `target` selects a stage to build
    in a multi-stage Containerfile."""

    tag: NonEmptyStr
    context_path: NonEmptyStr
    dockerfile: NonEmptyStr = "Dockerfile"
    build_args: dict[str, str] = Field(default_factory=dict)
    network: bool = False
    target: str | None = None


class BuildImageResult(_Frozen):
    """Outcome of a host-side Image Build.

    `image_id` is the built image's id/digest, empty when the build failed.
    `output` is the combined build log, streamed to the job log by the Master."""

    success: bool
    image_id: str
    output: str
    tag: NonEmptyStr


class RegistryCredentials(_Frozen):
    """Username/password an Image Push authenticates to a Registry with.

    The password is a secret value the pipeline fetched (typically via
    `secrets.get`); it never lands in the recorded step command and is scrubbed
    from the streamed push log."""

    username: NonEmptyStr
    password: NonEmptyStr


class PushImageRequest(_Frozen):
    """Asks the runner to push a tagged image to an external Registry.

    `tag` is the source reference in the host Local Image Store; `registry` is the
    target Registry host (e.g. ``registry.example.com:5000``). The runner pushes to
    ``<registry>/<tag>``. `credentials` authenticate the push when the Registry
    requires it; `tls_verify` defaults on and is only turned off for an insecure
    (HTTP/self-signed) Registry."""

    tag: NonEmptyStr
    registry: NonEmptyStr
    credentials: RegistryCredentials | None = None
    tls_verify: bool = True


class PushImageResult(_Frozen):
    """Outcome of a host-side Image Push.

    `reference` is the full pushed target reference (``<registry>/<tag>``).
    `digest` is the pushed manifest digest when the Registry reported one (empty
    otherwise). `output` is the combined push log, streamed to the job log."""

    success: bool
    reference: str
    digest: str
    output: str


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
