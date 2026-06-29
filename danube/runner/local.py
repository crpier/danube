"""`LocalContainerRunner`: the `Runner` protocol over rootless Podman.

This is the real execution backend behind the orchestrator's `Runner` dependency.
It maps each Danube job onto one Podman pod (`danube-job-<job_id>`) holding a
Coordinator and a Worker container that share a `/workspace` bind mount, execs
step commands in the Worker, streams their output, and tears everything down on
cleanup. It talks to Podman only through the `PodmanAPI` adapter, so it never
sees Podman wire formats; unit tests drive it with a recording fake adapter.

Security defaults follow `docs/architecture/local-runner.md` and `security.md`:
rootless (the adapter targets the rootless socket), never privileged, no host
network/PID/IPC namespaces, all capabilities dropped, `no-new-privileges`, CPU/
memory/pids limits, only the per-job workspace mounted, and a read-only root
filesystem with writable tmpfs scratch (`/tmp`, `/run`, `/var/tmp`) so real build
tooling still works. Egress is denied by default by attaching the pod to an `internal`
Podman network (`docs/architecture/networking.md`); allowlisted egress through a
proxy is handled separately.

`runner_state` rows are written for the pod and both containers when a `Database`
is supplied, so `reconcile()` (and `danube runner reconcile`) can compare tracked
state against the live, labelled Podman resources.
"""

from __future__ import annotations

import logging
import shutil
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path

import anyio.to_thread
from snekql.sqlite import Database, delete, insert, select

from danube.db.models import Job, RunnerState
from danube.domain.enums import JobStatus
from danube.domain.lifecycle import ACTIVE_STATES
from danube.domain.runner_types import (
    BuildImageRequest,
    BuildImageResult,
    CoordinatorExit,
    ExecResult,
    ExecStepRequest,
    JobHandle,
    PushImageRequest,
    PushImageResult,
    ReconcileReport,
    RunnerHealth,
    StartJobRequest,
)
from danube.runner.podman import (
    BuildSpec,
    ContainerSpec,
    ExecSpec,
    Mount,
    PodmanAPI,
    PodmanError,
    PodSpec,
    PushSpec,
    RegistryAuth,
    ResourceLimits,
    Tmpfs,
)

logger = logging.getLogger("danube.runner.local")

# Podman labels applied to every created resource. Required for reconciliation,
# cleanup, debugging, and metrics (see local-runner.md "Job Mapping").
LABEL_JOB_ID = "io.danube.job_id"
LABEL_PIPELINE_ID = "io.danube.pipeline_id"
LABEL_RESOURCE = "io.danube.resource"
LABEL_MANAGED = "io.danube.managed"

# Selector matching everything this runner owns, used by reconcile/list queries.
MANAGED_SELECTOR: Mapping[str, str] = {LABEL_MANAGED: "true"}

RESOURCE_POD = "pod"
RESOURCE_COORDINATOR = "coordinator"
RESOURCE_WORKER = "worker"

# `runner_state.kind` values mirror the resource labels; `status` records whether
# the resource is live or a cleanup left it behind.
STATE_RUNNING = "running"
STATE_CLEANUP_FAILED = "cleanup_failed"

WORKSPACE_MOUNT = "/workspace"

# Writable scratch carved out of the read-only rootfs. Real build tooling writes
# to `/tmp` (and expects `/run`, `/var/tmp` to be writable too); without these
# explicit tmpfs mounts a read-only rootfs would break those steps. Mounted
# explicitly rather than relying on libpod's read-only-rootfs defaults so the
# behaviour is the runner's, not the Podman version's (GitHub issue #51).
# S108 noqa below: these are in-container tmpfs mount destinations, not host
# temp files.
SCRATCH_TMPFS = (
    Tmpfs(destination="/tmp"),  # noqa: S108
    Tmpfs(destination="/run"),
    Tmpfs(destination="/var/tmp"),  # noqa: S108
)

# Default cgroup limits. Conservative caps that keep a runaway job from starving
# the appliance; per-pipeline limits can override these later.
DEFAULT_LIMITS = ResourceLimits(
    cpu_quota=200_000,  # 2 CPUs at the default 100ms period
    cpu_period=100_000,
    memory_bytes=2 * 1024**3,  # 2 GiB
    pids_limit=512,
)

# Internal Podman network the job pod attaches to: no route to the internet, so
# egress is denied by default.
DEFAULT_EGRESS_NETWORK = "danube-egress"

DEFAULT_COORDINATOR_IMAGE = "localhost/danube-coordinator:latest"
# Keep-alive command so the containers stay up for exec; the real Worker
# entrypoint replaces this later. A portable `sh` loop avoids relying on
# `sleep infinity`, which older BusyBox builds reject. Both containers idle on
# this; the Coordinator's pipeline is launched on demand via `start_coordinator`.
KEEP_ALIVE_COMMAND = ("/bin/sh", "-c", "while :; do sleep 3600; done")

# How the Coordinator container boots the SDK and runs the user pipeline
# (`docs/architecture/execution-model.md`, step 5). The Danube-provided image
# ships the `danube` distribution, so this module is importable; dev/test setups
# bind-mount the package and set `PYTHONPATH` via `coordinator_env`.
COORDINATOR_COMMAND = ("python", "-m", "danube.coordinator")


class CoordinatorNotStartedError(Exception):
    """`wait_for_coordinator` was called before `start_coordinator` for a job."""

    def __init__(self, job_id: str) -> None:
        self.job_id = job_id
        super().__init__(f"coordinator was not started for job {job_id!r}")


class ReconcileWithoutDatabaseError(Exception):
    """`reconcile()` was called on a runner constructed without a `Database`.

    Reconciliation reads the tracked `runner_state`/`jobs` rows, so it cannot run
    without one. Constructing the runner without a database is still valid for the
    job-execution path, which does not require persistence.
    """

    def __init__(self) -> None:
        super().__init__("reconcile requires a database; none was configured")


def pod_name(job_id: str) -> str:
    """The deterministic pod name for a job (also its reconciliation key)."""
    return f"danube-job-{job_id}"


@dataclass(frozen=True, slots=True)
class LocalRunnerConfig:
    """Tunable defaults for `LocalContainerRunner`.

    Bundled into one object so the runner constructor stays small; the defaults
    are the secure, single-host appliance settings.
    """

    coordinator_image: str = DEFAULT_COORDINATOR_IMAGE
    egress_network: str = DEFAULT_EGRESS_NETWORK
    limits: ResourceLimits = DEFAULT_LIMITS
    # How to launch the Coordinator pipeline, plus extra env and read-only mounts
    # the Coordinator container needs (e.g. the `danube` package and `PYTHONPATH`
    # for a stock-Python image that does not bundle the SDK). The Worker never
    # receives these.
    coordinator_command: tuple[str, ...] = COORDINATOR_COMMAND
    coordinator_env: Mapping[str, str] = field(default_factory=dict[str, str])
    coordinator_mounts: tuple[Mount, ...] = ()


_DEFAULT_CONFIG = LocalRunnerConfig()


class LocalContainerRunner:
    """Run Danube jobs as rootless Podman pods via a `PodmanAPI` adapter."""

    def __init__(
        self,
        podman: PodmanAPI,
        data_dir: Path | str,
        *,
        db: Database | None = None,
        config: LocalRunnerConfig = _DEFAULT_CONFIG,
    ) -> None:
        self._podman = podman
        self._data_dir = Path(data_dir)
        self._db = db
        self._coordinator_image = config.coordinator_image
        self._egress_network = config.egress_network
        self._limits = config.limits
        self._coordinator_command = config.coordinator_command
        self._coordinator_env = dict(config.coordinator_env)
        self._coordinator_mounts = tuple(config.coordinator_mounts)
        # Active Coordinator exec sessions, by job id, set by `start_coordinator`
        # and consumed by `wait_for_coordinator`.
        self._coordinator_execs: dict[str, str] = {}

    # --- lifecycle ----------------------------------------------------------

    async def start_job(self, request: StartJobRequest) -> JobHandle:
        job_id = request.job_id
        workspace = self._workspace_path(job_id)
        # Workspace creation is blocking filesystem IO; keep the event loop free.
        await anyio.to_thread.run_sync(
            partial(workspace.mkdir, parents=True, exist_ok=True)
        )

        await self._ensure_images(request.worker_image)
        await self._podman.ensure_network(
            self._egress_network, internal=True, labels=dict(MANAGED_SELECTOR)
        )

        name = pod_name(job_id)
        pod_id = await self._podman.create_pod(
            PodSpec(
                name=name,
                labels=self._labels(request, RESOURCE_POD),
                networks=(self._egress_network,),
            )
        )

        worker_id = await self._podman.create_container(
            self._container_spec(request, RESOURCE_WORKER, request.worker_image)
        )
        coordinator_id = await self._podman.create_container(
            self._container_spec(request, RESOURCE_COORDINATOR, self._coordinator_image)
        )

        await self._podman.start_pod(name)

        await self._record_state(
            job_id,
            (RESOURCE_POD, pod_id),
            (RESOURCE_WORKER, worker_id),
            (RESOURCE_COORDINATOR, coordinator_id),
        )

        return JobHandle(job_id=job_id, pod_id=pod_id, workspace_path=str(workspace))

    async def exec_step(self, job: JobHandle, request: ExecStepRequest) -> ExecResult:
        worker = self._worker_name(job.job_id)
        exec_id = await self._podman.exec_create(
            worker,
            ExecSpec(
                command=("/bin/sh", "-c", request.command),
                env=request.env,
                work_dir=WORKSPACE_MOUNT,
            ),
        )
        output = await self._podman.exec_start(exec_id)
        inspect = await self._podman.exec_inspect(exec_id)
        return ExecResult(
            exit_code=inspect.exit_code,
            stdout=output.stdout,
            stderr=output.stderr,
        )

    async def build_image(
        self, job: JobHandle, request: BuildImageRequest
    ) -> BuildImageResult:
        """Build an image on the host Podman from a workspace build context.

        The build runs on the same rootless Podman as the job pods, not inside the
        Worker, and tags into the shared Local Image Store. `RUN` networking is
        denied unless `request.network` opts in (`docs/adr/0001-host-side-image-build.md`)."""
        logger.info("building image %s for job %s", request.tag, job.job_id)
        result = await self._podman.build_image(
            BuildSpec(
                context_path=request.context_path,
                tag=request.tag,
                dockerfile=request.dockerfile,
                build_args=request.build_args,
                network=request.network,
                target=request.target,
            )
        )
        return BuildImageResult(
            success=result.success,
            image_id=result.image_id,
            output=result.output,
            tag=request.tag,
        )

    async def push_image(
        self, job: JobHandle, request: PushImageRequest
    ) -> PushImageResult:
        """Push a tagged image to an external Registry from the host Podman.

        The push runs on the same rootless Podman as the job pods, not inside the
        Worker, reading the source `request.tag` from the shared Local Image Store
        and uploading it to ``<registry>/<tag>``
        (`docs/adr/0001-host-side-image-build.md`)."""
        reference = f"{request.registry}/{request.tag}"
        logger.info(
            "pushing image %s to %s for job %s", request.tag, reference, job.job_id
        )
        auth = (
            RegistryAuth(
                username=request.credentials.username,
                password=request.credentials.password,
            )
            if request.credentials is not None
            else None
        )
        result = await self._podman.push_image(
            PushSpec(
                source=request.tag,
                destination=reference,
                auth=auth,
                tls_verify=request.tls_verify,
            )
        )
        return PushImageResult(
            success=result.success,
            reference=reference,
            digest=result.digest,
            output=result.output,
        )

    async def start_coordinator(self, job: JobHandle, env: Mapping[str, str]) -> None:
        """Launch the Coordinator pipeline inside its container via Podman exec.

        Creates (but does not yet run) the exec session for the entrypoint with
        the SDK connection env merged over the configured Coordinator env. The
        process runs when `wait_for_coordinator` starts the session. The Master's
        RPC is already serving by this point, so the Coordinator can call back
        immediately — no separate readiness handshake is needed.
        """
        coordinator = self._coordinator_name(job.job_id)
        exec_id = await self._podman.exec_create(
            coordinator,
            ExecSpec(
                command=self._coordinator_command,
                env={**self._coordinator_env, **env},
                work_dir=WORKSPACE_MOUNT,
            ),
        )
        self._coordinator_execs[job.job_id] = exec_id

    async def wait_for_coordinator(self, job: JobHandle) -> CoordinatorExit:
        """Run the Coordinator exec to completion and report its exit and output.

        Blocks while the entrypoint drives the pipeline (each `step.run()` reaches
        the Worker over the Master RPC, not this exec), then inspects the session
        for the exit code.
        """
        exec_id = self._coordinator_execs.get(job.job_id)
        if exec_id is None:
            raise CoordinatorNotStartedError(job.job_id)
        try:
            output = await self._podman.exec_start(exec_id)
            inspect = await self._podman.exec_inspect(exec_id)
        finally:
            _ = self._coordinator_execs.pop(job.job_id, None)
        return CoordinatorExit(
            exit_code=inspect.exit_code,
            stdout=output.stdout,
            stderr=output.stderr,
        )

    async def stop_job(self, job: JobHandle, reason: str) -> None:
        logger.info("stopping job %s pod: %s", job.job_id, reason)
        await self._podman.stop_pod(pod_name(job.job_id), grace_seconds=10)

    async def cleanup_job(self, job: JobHandle) -> None:
        """Remove the pod (and its containers), the workspace, and tracked state.

        Idempotent: removing an already-gone pod is a no-op in the adapter, and a
        missing workspace is ignored. A failure to remove the pod is recorded as
        a `cleanup_failed` runner_state row so reconciliation surfaces it.
        """
        name = pod_name(job.job_id)
        try:
            await self._podman.remove_pod(name, force=True)
        except PodmanError:
            logger.exception("failed to remove pod for job %s", job.job_id)
            await self._mark_cleanup_failed(job.job_id)
            raise
        await anyio.to_thread.run_sync(
            partial(shutil.rmtree, self._workspace_path(job.job_id), ignore_errors=True)
        )
        await self._clear_state(job.job_id)

    # --- introspection ------------------------------------------------------

    async def health(self) -> RunnerHealth:
        if not await self._podman.ping():
            return RunnerHealth(
                healthy=False, runtime="podman", detail="API socket unreachable"
            )
        try:
            version = await self._podman.version()
        except PodmanError as error:
            return RunnerHealth(healthy=False, runtime="podman", detail=str(error))
        return RunnerHealth(healthy=True, runtime="podman", version=version.version)

    async def reconcile(self) -> ReconcileReport:
        """Compare tracked `runner_state` with live, labelled Podman resources.

        Requires a `Database`. Reports the discrepancy categories from
        `local-runner.md`: pods for finished or unrecognised jobs, containers with
        no tracked state, workspaces for inactive jobs, missing pods for running
        jobs, and cleanups recorded as failed.
        """
        if self._db is None:
            raise ReconcileWithoutDatabaseError

        db = self._db
        job_status = await self._job_statuses(db)
        tracked = await self._tracked_job_ids(db)
        failed = await self._failed_cleanup_job_ids(db)

        pods = await self._podman.list_pods(MANAGED_SELECTOR)
        containers = await self._podman.list_containers(MANAGED_SELECTOR)
        live_pod_jobs = {
            job_id for pod in pods if (job_id := pod.labels.get(LABEL_JOB_ID))
        }

        # A pod is stale unless its job is still active: this covers both finished
        # (terminal) jobs and pods whose job_id has no `Job` row at all (deleted or
        # unknown), neither of which should still own a pod.
        stale_pods = sorted(
            pod.name
            for pod in pods
            if (job_id := pod.labels.get(LABEL_JOB_ID))
            and job_status.get(job_id) not in ACTIVE_STATES
        )
        orphaned_containers = sorted(
            container.name
            for container in containers
            if (job_id := container.labels.get(LABEL_JOB_ID)) and job_id not in tracked
        )
        missing_pods = sorted(
            job_id
            for job_id, status in job_status.items()
            if status in ACTIVE_STATES and job_id not in live_pod_jobs
        )
        workspace_dirs = await self._workspace_dirs()
        stale_workspaces = sorted(
            path.name
            for path in workspace_dirs
            if job_status.get(path.name) not in ACTIVE_STATES
        )

        return ReconcileReport(
            stale_pods=stale_pods,
            orphaned_containers=orphaned_containers,
            stale_workspaces=stale_workspaces,
            missing_pods=missing_pods,
            failed_cleanups=sorted(failed),
        )

    # --- spec construction --------------------------------------------------

    def _labels(self, request: StartJobRequest, resource: str) -> dict[str, str]:
        return {
            LABEL_JOB_ID: request.job_id,
            LABEL_PIPELINE_ID: request.pipeline_id,
            LABEL_RESOURCE: resource,
            LABEL_MANAGED: "true",
        }

    def _container_spec(
        self, request: StartJobRequest, resource: str, image: str
    ) -> ContainerSpec:
        workspace = self._workspace_path(request.job_id)
        workspace_mount = Mount(
            source=str(workspace), destination=WORKSPACE_MOUNT, read_only=False
        )
        # Only the Coordinator gets the extra read-only mounts (e.g. the SDK
        # package); the Worker is restricted to the shared workspace.
        extra = self._coordinator_mounts if resource == RESOURCE_COORDINATOR else ()
        return ContainerSpec(
            name=self._container_name(request.job_id, resource),
            image=image,
            pod=pod_name(request.job_id),
            labels=self._labels(request, resource),
            command=KEEP_ALIVE_COMMAND,
            env=request.env,
            mounts=(workspace_mount, *extra),
            tmpfs=SCRATCH_TMPFS,
            work_dir=WORKSPACE_MOUNT,
            privileged=False,
            read_only_rootfs=True,
            no_new_privileges=True,
            cap_drop=("ALL",),
            limits=self._limits,
        )

    def _container_name(self, job_id: str, resource: str) -> str:
        return f"{pod_name(job_id)}-{resource}"

    def _worker_name(self, job_id: str) -> str:
        return self._container_name(job_id, RESOURCE_WORKER)

    def _coordinator_name(self, job_id: str) -> str:
        return self._container_name(job_id, RESOURCE_COORDINATOR)

    async def _ensure_images(self, worker_image: str) -> None:
        for image in (worker_image, self._coordinator_image):
            if not await self._podman.image_exists(image):
                await self._podman.pull_image(image)

    def _workspace_path(self, job_id: str) -> Path:
        return self._data_dir / "workspaces" / job_id

    async def _workspace_dirs(self) -> list[Path]:
        # Directory scanning is blocking filesystem IO; run it off the event loop.
        return await anyio.to_thread.run_sync(self._list_workspace_dirs)

    def _list_workspace_dirs(self) -> list[Path]:
        root = self._data_dir / "workspaces"
        if not root.is_dir():
            return []
        return [path for path in root.iterdir() if path.is_dir()]

    # --- runner_state persistence ------------------------------------------

    async def _record_state(self, job_id: str, *resources: tuple[str, str]) -> None:
        if self._db is None:
            return
        async with self._db.transaction() as tx:
            for kind, external_id in resources:
                await tx.execute(
                    insert(
                        RunnerState(
                            id=str(uuid.uuid4()),
                            job_id=job_id,
                            kind=kind,
                            external_id=external_id,
                            status=STATE_RUNNING,
                        )
                    )
                )

    async def _clear_state(self, job_id: str) -> None:
        if self._db is None:
            return
        async with self._db.transaction() as tx:
            _ = await tx.execute(
                delete(RunnerState).where(RunnerState.job_id.eq(job_id))
            )

    async def _mark_cleanup_failed(self, job_id: str) -> None:
        if self._db is None:
            return
        async with self._db.transaction() as tx:
            await tx.execute(
                insert(
                    RunnerState(
                        id=str(uuid.uuid4()),
                        job_id=job_id,
                        kind=RESOURCE_POD,
                        external_id=pod_name(job_id),
                        status=STATE_CLEANUP_FAILED,
                    )
                )
            )

    async def _job_statuses(self, db: Database) -> dict[str, JobStatus]:
        async with db.transaction() as tx:
            rows = await tx.fetch_all(select(Job.id, Job.status).all())
        return {job_id: JobStatus(status) for job_id, status in rows}

    async def _tracked_job_ids(self, db: Database) -> set[str]:
        async with db.transaction() as tx:
            rows = await tx.fetch_all(select(RunnerState.job_id).all())
        return {job_id for job_id in rows if job_id is not None}

    async def _failed_cleanup_job_ids(self, db: Database) -> set[str]:
        async with db.transaction() as tx:
            rows = await tx.fetch_all(
                select(RunnerState.job_id).where(
                    RunnerState.status.eq(STATE_CLEANUP_FAILED)
                )
            )
        return {job_id for job_id in rows if job_id is not None}
