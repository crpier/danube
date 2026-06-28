"""The `Runner` protocol the Master orchestrator depends on.

Defined as a `typing.Protocol` so any backend (rootless Podman, the in-memory
`FakeRunner`, a future remote runner) satisfies it structurally without
inheriting from a shared base. Every method speaks Danube DTOs from
`danube.domain.runner_types`; none leak runtime concepts such as Podman.
"""

from collections.abc import Mapping
from typing import Protocol, runtime_checkable

from danube.domain.runner_types import (
    CoordinatorExit,
    ExecResult,
    ExecStepRequest,
    JobHandle,
    ReconcileReport,
    RunnerHealth,
    StartJobRequest,
)


@runtime_checkable
class Runner(Protocol):
    """Lifecycle a backend exposes to run one Danube job at a time."""

    async def start_job(self, request: StartJobRequest) -> JobHandle:
        """Create the job environment (workspace, pod, containers) and return a
        handle the Master passes back into later calls."""
        ...

    async def exec_step(self, job: JobHandle, request: ExecStepRequest) -> ExecResult:
        """Run a single command in the job's Worker and return its outcome."""
        ...

    async def start_coordinator(self, job: JobHandle, env: Mapping[str, str]) -> None:
        """Launch the Coordinator process that drives the pipeline.

        `env` carries the SDK's connection variables (`DANUBE_RPC_ADDRESS`,
        `DANUBE_JOB_ID`, `DANUBE_RPC_TOKEN`). Returns once the Coordinator has
        been started; use `wait_for_coordinator` to await its exit."""
        ...

    async def wait_for_coordinator(self, job: JobHandle) -> CoordinatorExit:
        """Block until the Coordinator process exits and report its outcome."""
        ...

    async def stop_job(self, job: JobHandle, reason: str) -> None:
        """Stop a running job's containers early (timeout, cancellation)."""
        ...

    async def cleanup_job(self, job: JobHandle) -> None:
        """Remove the pod, containers, network state, and workspace. Idempotent:
        safe to call after a job already stopped or was cleaned up."""
        ...

    async def reconcile(self) -> ReconcileReport:
        """Compare tracked runner state with live resources and report drift."""
        ...

    async def health(self) -> RunnerHealth:
        """Report runtime health and version."""
        ...
