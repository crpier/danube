"""In-memory `Runner` for testing the orchestrator without Podman.

`FakeRunner` records every call in order, returns scripted `ExecResult`s
(configurable per command or as a global sequence), tracks each job's state, and
makes `cleanup_job` idempotent. Executing a step against a cleaned-up or unknown
job raises `JobNotActiveError`.
"""

from collections import deque
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from enum import Enum, auto

from danube.domain.runner_types import (
    BuildImageRequest,
    BuildImageResult,
    CoordinatorExit,
    ExecResult,
    ExecStepRequest,
    JobHandle,
    ReconcileReport,
    RunnerHealth,
    StartJobRequest,
)

# A stand-in for the Coordinator process: given the SDK connection env, it drives
# the pipeline (typically a real `DanubeClient` against an in-process app) and
# returns the process exit code. Raising simulates a crash (non-zero exit).
CoordinatorProgram = Callable[[Mapping[str, str]], Awaitable[int]]

# Calling a model constructor in a default argument trips pyright's
# `reportCallInDefaultInitializer`, so the fallbacks live as module constants.
_DEFAULT_RESULT = ExecResult(exit_code=0, stdout="", stderr="")
_DEFAULT_HEALTH = RunnerHealth(healthy=True, runtime="fake")
_DEFAULT_BUILD = BuildImageResult(
    success=True, image_id="sha256:fake", output="", tag="fake:latest"
)


@dataclass(frozen=True, slots=True)
class RecordedCall:
    """One method invocation captured by `FakeRunner`, in call order.

    `args` holds the call's positional payload (e.g. the request DTO, or a
    `(JobHandle, reason)` pair) so tests can assert the exact sequence."""

    method: str
    args: tuple[object, ...]


class _JobState(Enum):
    ACTIVE = auto()
    STOPPED = auto()
    CLEANED = auto()


class JobNotActiveError(Exception):
    """Raised when a step is executed against a job that is unknown to the runner
    or no longer active (stopped or cleaned up)."""

    def __init__(self, job_id: str) -> None:
        self.job_id = job_id
        super().__init__(f"no active job for job_id: {job_id!r}")


class FakeRunner:
    """Scriptable in-memory implementation of the `Runner` protocol."""

    def __init__(
        self,
        *,
        default_result: ExecResult = _DEFAULT_RESULT,
        health_result: RunnerHealth = _DEFAULT_HEALTH,
        reconcile_result: ReconcileReport | None = None,
        coordinator: CoordinatorProgram | None = None,
        coordinator_exit_code: int = 0,
    ) -> None:
        self.default_result = default_result
        self.health_result = health_result
        self.reconcile_result = reconcile_result or ReconcileReport()
        self._coordinator = coordinator
        self._coordinator_exit_code = coordinator_exit_code

        self.calls: list[RecordedCall] = []
        self._states: dict[str, _JobState] = {}
        self._results_by_command: dict[str, deque[ExecResult]] = {}
        self._result_sequence: deque[ExecResult] = deque()
        self._builds_by_tag: dict[str, deque[BuildImageResult]] = {}
        self._coordinator_env: dict[str, str] = {}

    @property
    def method_names(self) -> list[str]:
        """The recorded call methods in order, a convenience for assertions."""
        return [call.method for call in self.calls]

    def script_command(self, command: str, *results: ExecResult) -> None:
        """Queue `results` to be returned, in order, for steps whose command
        equals `command`. Takes precedence over any global sequence."""
        self._results_by_command.setdefault(command, deque()).extend(results)

    def script_sequence(self, *results: ExecResult) -> None:
        """Queue `results` to be returned, in order, for any step without a
        command-specific script."""
        self._result_sequence.extend(results)

    def script_build(self, tag: str, *results: BuildImageResult) -> None:
        """Queue `results` to be returned, in order, for builds of image `tag`."""
        self._builds_by_tag.setdefault(tag, deque()).extend(results)

    async def start_job(self, request: StartJobRequest) -> JobHandle:
        self.calls.append(RecordedCall("start_job", (request,)))
        self._states[request.job_id] = _JobState.ACTIVE
        return JobHandle(
            job_id=request.job_id,
            pod_id=f"fake-pod-{request.job_id}",
            workspace_path=f"/fake/workspaces/{request.job_id}",
        )

    async def exec_step(self, job: JobHandle, request: ExecStepRequest) -> ExecResult:
        self.calls.append(RecordedCall("exec_step", (job, request)))
        if self._states.get(job.job_id) is not _JobState.ACTIVE:
            raise JobNotActiveError(job.job_id)
        return self._next_result(request.command)

    async def build_image(
        self, job: JobHandle, request: BuildImageRequest
    ) -> BuildImageResult:
        self.calls.append(RecordedCall("build_image", (job, request)))
        if self._states.get(job.job_id) is not _JobState.ACTIVE:
            raise JobNotActiveError(job.job_id)
        scripted = self._builds_by_tag.get(request.tag)
        if scripted:
            return scripted.popleft()
        return _DEFAULT_BUILD

    async def start_coordinator(self, job: JobHandle, env: Mapping[str, str]) -> None:
        self.calls.append(RecordedCall("start_coordinator", (job, dict(env))))
        if self._states.get(job.job_id) is not _JobState.ACTIVE:
            raise JobNotActiveError(job.job_id)
        self._coordinator_env = dict(env)

    async def wait_for_coordinator(self, job: JobHandle) -> CoordinatorExit:
        self.calls.append(RecordedCall("wait_for_coordinator", (job,)))
        if self._coordinator is None:
            return CoordinatorExit(exit_code=self._coordinator_exit_code)
        try:
            code = await self._coordinator(self._coordinator_env)
        except Exception as error:  # a crashing Coordinator exits non-zero
            return CoordinatorExit(exit_code=1, stderr=str(error))
        return CoordinatorExit(exit_code=code)

    async def stop_job(self, job: JobHandle, reason: str) -> None:
        self.calls.append(RecordedCall("stop_job", (job, reason)))
        if self._states.get(job.job_id) is _JobState.ACTIVE:
            self._states[job.job_id] = _JobState.STOPPED

    async def cleanup_job(self, job: JobHandle) -> None:
        # Idempotent: recorded every time, but only mutates state to CLEANED once.
        self.calls.append(RecordedCall("cleanup_job", (job,)))
        self._states[job.job_id] = _JobState.CLEANED

    async def reconcile(self) -> ReconcileReport:
        self.calls.append(RecordedCall("reconcile", ()))
        return self.reconcile_result

    async def health(self) -> RunnerHealth:
        self.calls.append(RecordedCall("health", ()))
        return self.health_result

    def _next_result(self, command: str) -> ExecResult:
        scripted = self._results_by_command.get(command)
        if scripted:
            return scripted.popleft()
        if self._result_sequence:
            return self._result_sequence.popleft()
        return self.default_result
