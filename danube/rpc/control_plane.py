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

Scope per issue #8: real Worker exec is provided by the injected `Runner` (the
`FakeRunner` in tests, Podman in issue #9); secret decryption is a pluggable hook
that defaults to a UTF-8 decode of the stored ciphertext.
"""

import hmac
import logging
import secrets as secrets_module
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from snekql.sqlite import Database, insert, select, update

from danube.db.models import Artifact, Job, Secret, Step
from danube.domain.enums import JobStatus, StepStatus
from danube.domain.lifecycle import transition
from danube.domain.runner_types import ExecStepRequest, JobHandle
from danube.orchestrator.log_writer import LogWriter
from danube.rpc.schemas import (
    ReportStatusResponse,
    RunStepRequest,
    RunStepResponse,
    UploadArtifactResponse,
)
from danube.runner.base import Runner

logger = logging.getLogger("danube.rpc")

SecretDecryptor = Callable[[bytes], str]

ARTIFACT_ROOT = Path("/var/lib/danube/artifacts")
_SECRET_MASK = "***"  # noqa: S105 - log redaction mask, not a credential


def _default_decrypt(ciphertext: bytes) -> str:
    """Minimal stand-in for real AES-256-GCM decryption (issue #8 out of scope).

    Treats the stored ciphertext as UTF-8 plaintext so the control plane has a
    working secret path without pulling in the encryption key machinery yet.
    """
    return ciphertext.decode("utf-8")


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


@dataclass(slots=True)
class JobSession:
    """Live state for one running job that RPC calls are checked against."""

    job_id: str
    pipeline_id: str
    token: str
    handle: JobHandle
    log: LogWriter
    # Authorized plaintext secrets, by key, loaded once when the session opens.
    secret_values: dict[str, str]
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
        decrypt: SecretDecryptor = _default_decrypt,
    ) -> None:
        self._runner = runner
        self._db = db
        self._data_dir = Path(data_dir)
        self._decrypt = decrypt
        self._sessions: dict[str, JobSession] = {}

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
        secret_values = await self._load_secrets(pipeline_id)
        log = LogWriter(self._log_path(job_id))
        _ = await log.__aenter__()
        session = JobSession(
            job_id=job_id,
            pipeline_id=pipeline_id,
            token=resolved_token,
            handle=handle,
            log=log,
            secret_values=secret_values,
        )
        self._sessions[job_id] = session
        return session

    async def close_session(self, job_id: str) -> None:
        """Drop a job's session and close its log. Idempotent."""
        session = self._sessions.pop(job_id, None)
        if session is not None:
            await session.log.__aexit__(None, None, None)

    async def run_step(
        self, job_id: str, token: str, request: RunStepRequest
    ) -> RunStepResponse:
        """Exec one command in the job's Worker, log scrubbed output, return code."""
        session = self._authorize(job_id, token)
        sequence = session.next_sequence()
        step_id = str(uuid.uuid4())
        name = request.name or f"step-{sequence}"
        await self._begin_step(step_id, job_id, sequence, name, request.command)

        result = await self._runner.exec_step(
            session.handle,
            ExecStepRequest(
                command=request.command,
                env=request.env,
                timeout_seconds=request.timeout_seconds,
            ),
        )
        combined = result.stdout + result.stderr
        scrubbed = scrub_secrets(combined, list(session.secret_values.values()))
        start, end = await session.log.write(scrubbed)
        await self._finish_step(step_id, result.exit_code, start, end)

        return RunStepResponse(
            exit_code=result.exit_code,
            stdout=result.stdout if request.capture_output else None,
            stderr=result.stderr if request.capture_output else None,
        )

    async def get_secret(self, job_id: str, token: str, key: str) -> str:
        """Return an authorized secret value, or raise `SecretNotAuthorizedError`."""
        session = self._authorize(job_id, token)
        value = session.secret_values.get(key)
        if value is None:
            # Do not reveal whether the key exists for another pipeline.
            raise SecretNotAuthorizedError(key)
        return value

    async def upload_artifact(
        self, job_id: str, token: str, name: str, path: str, size_bytes: int
    ) -> UploadArtifactResponse:
        """Record artifact metadata under the job's artifact directory.

        The real byte transfer from the workspace lands in a later issue; here the
        Coordinator-reported `path`/`size_bytes` are persisted so the artifact is
        tracked and downloadable metadata exists.
        """
        session = self._authorize(job_id, token)
        artifact_id = str(uuid.uuid4())
        destination = ARTIFACT_ROOT / job_id / name
        logger.info("job %s uploaded artifact %r from %s", session.job_id, name, path)
        async with self._db.transaction() as tx:
            await tx.execute(
                insert(
                    Artifact(
                        id=artifact_id,
                        job_id=job_id,
                        name=name,
                        path=str(destination),
                        size_bytes=size_bytes,
                    )
                )
            )
        return UploadArtifactResponse(
            artifact_id=artifact_id, name=name, size_bytes=size_bytes
        )

    async def report_status(
        self, job_id: str, token: str, state: JobStatus, detail: str | None
    ) -> ReportStatusResponse:
        """Apply a Coordinator-reported job status transition.

        The move is validated through `danube.domain.lifecycle`, so an illegal
        transition raises `InvalidTransition` (mapped to 409 by the route).
        """
        _ = self._authorize(job_id, token)
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

    def _authorize(self, job_id: str, token: str) -> JobSession:
        session = self._sessions.get(job_id)
        if session is None:
            raise SessionNotActiveError(job_id)
        if not hmac.compare_digest(session.token, token):
            raise InvalidTokenError(job_id)
        return session

    async def _load_secrets(self, pipeline_id: str) -> dict[str, str]:
        """Load the pipeline's secrets plus global (pipeline-less) secrets."""
        async with self._db.transaction() as tx:
            rows = await tx.fetch_all(
                select(Secret).where(
                    Secret.pipeline_id.eq(pipeline_id) | Secret.pipeline_id.is_null()
                )
            )
        return {row.key: self._decrypt(row.value_encrypted) for row in rows}

    def _log_path(self, job_id: str) -> Path:
        return self._data_dir / "logs" / f"{job_id}.log"

    async def _begin_step(
        self, step_id: str, job_id: str, sequence: int, name: str, command: str
    ) -> None:
        async with self._db.transaction() as tx:
            await tx.execute(
                insert(
                    Step(
                        id=step_id,
                        job_id=job_id,
                        name=name,
                        sequence=sequence,
                        command=command,
                        status=StepStatus.RUNNING,
                        started_at=_now(),
                    )
                )
            )

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


_TERMINAL_STATES = frozenset(
    {
        JobStatus.SUCCESS,
        JobStatus.FAILURE,
        JobStatus.TIMEOUT,
        JobStatus.CANCELLED,
    }
)
