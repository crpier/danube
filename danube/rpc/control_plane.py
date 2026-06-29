"""Server side of the Coordinator <-> Master RPC control plane.

`ControlPlane` owns the set of *active job sessions*. A session is opened once the
runner has created a job's environment and the Coordinator is about to start; it
holds the runner `JobHandle`, a job-scoped bearer token, the authorized secret
values, and an open `LogWriter`. Every RPC (`run_step`, `get_secret`,
`upload_artifact`, `report_status`) is validated against a live session: an
unknown/closed job or a mismatched token is rejected before any work happens.

All command execution is mediated here: the Coordinator never talks to the Worker
directly (`docs/architecture/execution-model.md`, Communication Strategy). Secrets
are scrubbed from the captured output before it is written to the job log
(Secrets Access), while the RPC response back to the Coordinator is left intact —
it already holds the value it asked for.

Real Worker exec is provided by the injected `Runner` (the `FakeRunner` in tests,
a Podman-backed runner in production); secret loading, AES-256-GCM decryption, and
per-job caching are owned by the injected `SecretService`.
"""

import hmac
import logging
import secrets as secrets_module
import shutil
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import anyio
import anyio.to_thread
from snekql.sqlite import Database, insert, select, update

from danube.db.models import Artifact, Job, Step
from danube.domain.enums import JobStatus, StepKind, StepStatus
from danube.domain.lifecycle import transition
from danube.domain.runner_types import (
    BuildImageRequest as RunnerBuildImageRequest,
)
from danube.domain.runner_types import (
    ExecStepRequest,
    JobHandle,
)
from danube.observability import Tracer
from danube.orchestrator.log_writer import LogWriter
from danube.rpc.schemas import (
    BuildImageRequest,
    BuildImageResponse,
    ReportStatusResponse,
    RunStepRequest,
    RunStepResponse,
    UploadArtifactResponse,
)
from danube.runner.base import Runner
from danube.security import SecretService

logger = logging.getLogger("danube.rpc")

_SECRET_MASK = "***"  # noqa: S105 - log redaction mask, not a credential


def scrub_secrets(text: str, values: list[str]) -> str:
    """Replace every occurrence of a known secret value with a fixed mask.

    Empty values are ignored so a blank secret never masks the whole stream.
    """
    for value in values:
        if value:
            text = text.replace(value, _SECRET_MASK)
    return text


class RpcError(Exception):
    """Base for control-plane authorization/validation failures."""


class SessionNotActiveError(RpcError):
    """No open session for the job: it is unknown, never started, or finished."""

    def __init__(self, job_id: str) -> None:
        self.job_id = job_id
        super().__init__(f"no active job session for {job_id!r}")


class InvalidTokenError(RpcError):
    """The job exists but the presented bearer token does not match its session."""

    def __init__(self, job_id: str) -> None:
        self.job_id = job_id
        super().__init__(f"invalid token for job {job_id!r}")


class SecretNotAuthorizedError(RpcError):
    """The pipeline is not authorized for the requested secret key."""

    def __init__(self, key: str) -> None:
        self.key = key
        super().__init__(f"secret {key!r} is not accessible to this pipeline")


class ArtifactSourceError(RpcError):
    """The artifact name or its source path inside the workspace is invalid.

    Covers a name with a path separator, a source that escapes the workspace, and
    a source that does not exist when the upload is requested. `reason` describes
    which of those failed; `target` is the offending name or path.
    """

    def __init__(self, target: str, reason: str) -> None:
        self.target = target
        self.reason = reason
        super().__init__(f"artifact {target!r} {reason}")


class BuildContextError(RpcError):
    """The build context or Containerfile for an Image Build is invalid.

    Covers a context that escapes the workspace or is not a directory, and a
    Containerfile that escapes the context or does not exist. `reason` describes
    which failed; `target` is the offending context or dockerfile path.
    """

    def __init__(self, target: str, reason: str) -> None:
        self.target = target
        self.reason = reason
        super().__init__(f"build context {target!r} {reason}")


@dataclass(slots=True)
class JobSession:
    """Live state for one running job that RPC calls are checked against."""

    job_id: str
    pipeline_id: str
    token: str
    handle: JobHandle
    log: LogWriter
    _sequence: int = field(default=0, init=False)

    def next_sequence(self) -> int:
        self._sequence += 1
        return self._sequence


def _now() -> datetime:
    return datetime.now(UTC)


class ControlPlane:
    """Registry of active job sessions plus the RPC operations over them."""

    def __init__(
        self,
        runner: Runner,
        db: Database,
        data_dir: Path | str,
        *,
        secret_service: SecretService,
        tracer: Tracer | None = None,
    ) -> None:
        self._runner = runner
        self._db = db
        self._data_dir = Path(data_dir)
        # An explicit cipher-backed service is required: there is no plaintext
        # fallback, so a misconfigured key fails closed rather than serving
        # ciphertext as if it were the secret value.
        self._secrets = secret_service
        self._sessions: dict[str, JobSession] = {}
        # No-op unless tracing is enabled; lets `run_step` span the per-step RPC
        # and Worker exec (`docs/architecture/observability.md`, Job Execution).
        self._tracer = tracer or Tracer()

    async def open_session(
        self,
        *,
        job_id: str,
        pipeline_id: str,
        handle: JobHandle,
        token: str | None = None,
    ) -> JobSession:
        """Register an active session for a job whose environment is ready.

        Loads the pipeline's authorized secrets, opens the job's append-only log,
        mints a job-scoped token if one is not supplied, and returns the session.
        """
        resolved_token = token or secrets_module.token_urlsafe(32)
        await self._secrets.load_for_job(job_id, pipeline_id)
        log = LogWriter(self._log_path(job_id))
        _ = await log.__aenter__()
        session = JobSession(
            job_id=job_id,
            pipeline_id=pipeline_id,
            token=resolved_token,
            handle=handle,
            log=log,
        )
        self._sessions[job_id] = session
        return session

    async def close_session(self, job_id: str) -> None:
        """Drop a job's session, clear its cached secrets, and close its log.

        Idempotent.
        """
        self._secrets.clear_job(job_id)
        session = self._sessions.pop(job_id, None)
        if session is not None:
            await session.log.__aexit__(None, None, None)

    async def run_step(
        self, job_id: str, token: str, request: RunStepRequest
    ) -> RunStepResponse:
        """Exec one command in the job's Worker, log scrubbed output, return code."""
        session = await self._authorize(job_id, token)
        secret_values = self._secrets.active_values(session.job_id)
        sequence = session.next_sequence()
        step_id = str(uuid.uuid4())
        name = request.name or f"step-{sequence}"
        with self._tracer.span(f"execute step {sequence}", job_id=job_id, step=name):
            # Scrub the command too: a secret inlined in the command string must not
            # be persisted verbatim to the step record (Secrets Access,
            # execution-model.md).
            logged_command = scrub_secrets(request.command, secret_values)
            await self._begin_step(
                Step(
                    id=step_id,
                    job_id=job_id,
                    name=name,
                    sequence=sequence,
                    command=logged_command,
                    kind=StepKind.RUN,
                    status=StepStatus.RUNNING,
                    started_at=_now(),
                )
            )

            with self._tracer.span("runner exec", job_id=job_id, step=name):
                result = await self._runner.exec_step(
                    session.handle,
                    ExecStepRequest(command=request.command, env=request.env),
                )
            combined = result.stdout + result.stderr
            scrubbed = scrub_secrets(combined, secret_values)
            start, end = await session.log.write(scrubbed)
            await self._finish_step(step_id, result.exit_code, start, end)

        return RunStepResponse(
            exit_code=result.exit_code,
            stdout=result.stdout if request.capture_output else None,
            stderr=result.stderr if request.capture_output else None,
        )

    async def build_image(
        self, job_id: str, token: str, request: BuildImageRequest
    ) -> BuildImageResponse:
        """Build an image on the host Podman from a workspace build context.

        Records a `kind=build` step, drives the build through the runner (host
        Podman, never the Worker — `docs/adr/0001-host-side-image-build.md`),
        streams the scrubbed build output to the job log, and marks the step
        success/failure by the build outcome. The context directory and the
        Containerfile within it are resolved inside the job workspace, so a path
        that escapes it or does not exist is rejected before any build runs."""
        session = await self._authorize(job_id, token)
        secret_values = self._secrets.active_values(session.job_id)
        sequence = session.next_sequence()
        step_id = str(uuid.uuid4())
        name = request.name or f"build-{sequence}"
        context_path = await anyio.to_thread.run_sync(
            _resolve_build_context,
            session.handle.workspace_path,
            request.context,
            request.dockerfile,
        )
        command = f"build {request.tag} from {request.context}/{request.dockerfile}"
        with self._tracer.span(f"build image {sequence}", job_id=job_id, step=name):
            await self._begin_step(
                Step(
                    id=step_id,
                    job_id=job_id,
                    name=name,
                    sequence=sequence,
                    command=scrub_secrets(command, secret_values),
                    kind=StepKind.BUILD,
                    status=StepStatus.RUNNING,
                    started_at=_now(),
                )
            )
            with self._tracer.span("runner build", job_id=job_id, step=name):
                result = await self._runner.build_image(
                    session.handle,
                    RunnerBuildImageRequest(
                        tag=request.tag,
                        context_path=str(context_path),
                        dockerfile=request.dockerfile,
                        build_args=request.build_args,
                        network=request.network,
                        target=request.target,
                    ),
                )
            scrubbed = scrub_secrets(result.output, secret_values)
            start, end = await session.log.write(scrubbed)
            exit_code = 0 if result.success else 1
            await self._finish_step(step_id, exit_code, start, end)
        return BuildImageResponse(
            tag=result.tag, digest=result.image_id, success=result.success
        )

    async def get_secret(self, job_id: str, token: str, key: str) -> str:
        """Return an authorized secret value, or raise `SecretNotAuthorizedError`."""
        _ = await self._authorize(job_id, token)
        value = self._secrets.get(job_id, key)
        if value is None:
            # Do not reveal whether the key exists for another pipeline.
            raise SecretNotAuthorizedError(key)
        return value

    async def upload_artifact(
        self, job_id: str, token: str, name: str, path: str
    ) -> UploadArtifactResponse:
        """Copy an artifact out of the job workspace and record its metadata.

        The bytes are captured here, while the session is live, so an artifact is
        always persisted before the runner deletes the workspace on cleanup
        (`docs/architecture/execution-model.md`, Workspace). The stored size is
        computed from the bytes actually written, so directory artifacts are
        recorded accurately and the Coordinator cannot misreport a size. The source
        must live inside the workspace and exist now, otherwise it is rejected.
        """
        session = await self._authorize(job_id, token)
        if "/" in name or "\\" in name or name in {".", ".."}:
            raise ArtifactSourceError(name, "is not a valid artifact name")
        destination = self._artifact_path(job_id, name)
        # Path resolution, containment check, and copy all touch the filesystem, so
        # they run together in a worker thread off the event loop.
        stored_size = await anyio.to_thread.run_sync(
            _capture_artifact, session.handle.workspace_path, path, destination
        )
        artifact_id = str(uuid.uuid4())
        logger.info("job %s uploaded artifact %r from %s", session.job_id, name, path)
        async with self._db.transaction() as tx:
            await tx.execute(
                insert(
                    Artifact(
                        id=artifact_id,
                        job_id=job_id,
                        name=name,
                        path=str(destination),
                        size_bytes=stored_size,
                    )
                )
            )
        return UploadArtifactResponse(
            artifact_id=artifact_id, name=name, size_bytes=stored_size
        )

    async def report_status(
        self, job_id: str, token: str, state: JobStatus, detail: str | None
    ) -> ReportStatusResponse:
        """Apply a Coordinator-reported job status transition.

        The move is validated through `danube.domain.lifecycle`, so an illegal
        transition raises `InvalidTransition` (mapped to 409 by the route).
        """
        _ = await self._authorize(job_id, token)
        async with self._db.transaction() as tx:
            job = await tx.fetch_one(select(Job).where(Job.id.eq(job_id)))
            new = transition(JobStatus(job.status), state)
            query = update(Job).set(Job.status.to(new))
            if new in _TERMINAL_STATES:
                query = update(Job).set(
                    Job.status.to(new),
                    Job.finished_at.to(_now()),
                    Job.failure_reason.to(detail),
                )
            _ = await tx.execute(query.where(Job.id.eq(job_id)))
        return ReportStatusResponse(status=new)

    async def _authorize(self, job_id: str, token: str) -> JobSession:
        session = self._sessions.get(job_id)
        if session is None:
            raise SessionNotActiveError(job_id)
        if not hmac.compare_digest(session.token, token):
            raise InvalidTokenError(job_id)
        # An open session is not enough: the job must still be active in the DB
        # (a job that reached a terminal state rejects further RPCs), per the
        # Master's "job is active" check in execution-model.md (Secrets Access).
        async with self._db.transaction() as tx:
            job = await tx.fetch_one(select(Job).where(Job.id.eq(job_id)))
        if JobStatus(job.status) in _TERMINAL_STATES:
            raise SessionNotActiveError(job_id)
        return session

    def _log_path(self, job_id: str) -> Path:
        return self._data_dir / "logs" / f"{job_id}.log"

    def _artifact_path(self, job_id: str, name: str) -> Path:
        return self._data_dir / "artifacts" / job_id / name

    async def _begin_step(self, step: Step) -> None:
        async with self._db.transaction() as tx:
            await tx.execute(insert(step))

    async def _finish_step(
        self, step_id: str, exit_code: int, start: int, end: int
    ) -> None:
        status = StepStatus.FAILURE if exit_code != 0 else StepStatus.SUCCESS
        async with self._db.transaction() as tx:
            _ = await tx.execute(
                update(Step)
                .set(
                    Step.status.to(status),
                    Step.exit_code.to(exit_code),
                    Step.finished_at.to(_now()),
                    Step.log_offset_start.to(start),
                    Step.log_offset_end.to(end),
                )
                .where(Step.id.eq(step_id))
            )


def _tree_size(path: Path) -> int:
    """Total size in bytes of ``path``: its own size if a file, otherwise the sum
    of every regular file beneath it."""
    if path.is_file():
        return path.stat().st_size
    return sum(child.stat().st_size for child in path.rglob("*") if child.is_file())


def _copy_artifact(source: Path, destination: Path) -> int:
    """Copy ``source`` (a file or directory tree) to ``destination`` and return the
    total size of the stored bytes. Runs in a worker thread off the event loop."""
    if destination.is_dir():
        shutil.rmtree(destination)
    elif destination.exists():
        destination.unlink()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        _ = shutil.copytree(source, destination)
    else:
        _ = shutil.copy2(source, destination)
    return _tree_size(destination)


def _resolve_build_context(workspace_path: str, context: str, dockerfile: str) -> Path:
    """Resolve and validate an Image Build context inside the job workspace.

    Returns the absolute context directory. Runs in a worker thread; raises
    `BuildContextError` if the context escapes the workspace or is not a
    directory, or if the Containerfile escapes the context or does not exist."""
    workspace = Path(workspace_path).resolve()
    context_dir = (workspace / context).resolve()
    if not context_dir.is_relative_to(workspace):
        raise BuildContextError(context, "escapes the job workspace")
    if not context_dir.is_dir():
        raise BuildContextError(context, "is not a directory in the job workspace")
    dockerfile_path = (context_dir / dockerfile).resolve()
    if not dockerfile_path.is_relative_to(context_dir):
        raise BuildContextError(dockerfile, "escapes the build context")
    if not dockerfile_path.is_file():
        raise BuildContextError(dockerfile, "does not exist in the build context")
    return context_dir


def _capture_artifact(workspace_path: str, path: str, destination: Path) -> int:
    """Resolve ``path`` inside the workspace, copy it to ``destination``, and return
    the stored size. Runs in a worker thread; raises `ArtifactSourceError` if the
    source escapes the workspace or does not exist."""
    workspace = Path(workspace_path).resolve()
    source = (workspace / path).resolve()
    if not source.is_relative_to(workspace):
        raise ArtifactSourceError(path, "escapes the job workspace")
    if not source.exists():
        raise ArtifactSourceError(path, "does not exist in the job workspace")
    return _copy_artifact(source, destination)


_TERMINAL_STATES = frozenset(
    {
        JobStatus.SUCCESS,
        JobStatus.FAILURE,
        JobStatus.TIMEOUT,
        JobStatus.CANCELLED,
    }
)
