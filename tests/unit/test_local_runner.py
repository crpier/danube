"""Unit tests for `LocalContainerRunner` against a recording fake `PodmanAPI`.

No Podman: a `FakePodman` records every adapter call in order and returns
scripted results. The tests assert the runner issues the expected pod/container/
exec/cleanup calls with the correct Danube labels and hardened security options,
that `exec_step` maps a Podman exec onto an `ExecResult`, and that `reconcile`
classifies drift against an in-memory database.
"""

import shutil
import tempfile
from collections.abc import AsyncGenerator, Generator, Mapping
from dataclasses import dataclass
from pathlib import Path

from snekql.sqlite import Database, insert, select
from snektest import assert_eq, assert_raises, fixture, load_fixture, test

from danube.db import open_database
from danube.db.models import Job, Pipeline, RunnerState, Team
from danube.domain.enums import JobStatus, TriggerType
from danube.domain.runner_types import (
    BuildImageRequest,
    ExecStepRequest,
    JobHandle,
    PushImageRequest,
    RegistryCredentials,
    StartJobRequest,
)
from danube.runner.base import Runner
from danube.runner.local import (
    DEFAULT_EGRESS_NETWORK,
    DEFAULT_LIMITS,
    DEFAULT_OUTBOUND_NETWORK,
    LABEL_JOB_ID,
    LABEL_MANAGED,
    LABEL_PIPELINE_ID,
    LABEL_RESOURCE,
    RESOURCE_COORDINATOR,
    RESOURCE_WORKER,
    SCRATCH_TMPFS,
    CoordinatorNotStartedError,
    LocalContainerRunner,
    LocalRunnerConfig,
    ReconcileWithoutDatabaseError,
    pod_name,
)
from danube.runner.podman import (
    BuildResult,
    BuildSpec,
    ContainerSpec,
    ExecInspect,
    ExecOutput,
    ExecSpec,
    Mount,
    PodmanAPI,
    PodmanError,
    PodmanVersion,
    PodSpec,
    PushResult,
    PushSpec,
    ResourceSummary,
)
from danube.runner.podman.adapter import _container_body

START = StartJobRequest(job_id="j1", pipeline_id="p1", worker_image="busybox:latest")


@dataclass
class Call:
    """One recorded `PodmanAPI` invocation: method name and keyword payload."""

    method: str
    payload: dict[str, object]


class FakePodman:
    """Recording `PodmanAPI` test double with scriptable exec/list results."""

    def __init__(self) -> None:
        self.calls: list[Call] = []
        self.existing_images: set[str] = set()
        self.exec_output = ExecOutput(stdout="", stderr="")
        self.exec_result = ExecInspect(exit_code=0, running=False)
        self.build_result = BuildResult(
            success=True, image_id="sha256:built", output="step 1/1\n"
        )
        self.push_result = PushResult(
            success=True, digest="sha256:pushed", output="pushing\n"
        )
        self.pods: list[ResourceSummary] = []
        self.containers: list[ResourceSummary] = []
        self.remove_pod_error: PodmanError | None = None
        self.alive = True

    @property
    def methods(self) -> list[str]:
        return [call.method for call in self.calls]

    def _record(self, method: str, **payload: object) -> None:
        self.calls.append(Call(method, payload))

    async def ping(self) -> bool:
        self._record("ping")
        return self.alive

    async def version(self) -> PodmanVersion:
        self._record("version")
        return PodmanVersion(version="5.0.0", api_version="5.0.0")

    async def image_exists(self, reference: str) -> bool:
        self._record("image_exists", reference=reference)
        return reference in self.existing_images

    async def pull_image(self, reference: str) -> None:
        self._record("pull_image", reference=reference)
        self.existing_images.add(reference)

    async def ensure_network(
        self, name: str, *, internal: bool, labels: Mapping[str, str]
    ) -> None:
        self._record(
            "ensure_network", name=name, internal=internal, labels=dict(labels)
        )

    async def create_pod(self, spec: PodSpec) -> str:
        self._record("create_pod", spec=spec)
        return f"pod-{spec.name}"

    async def start_pod(self, pod: str) -> None:
        self._record("start_pod", pod=pod)

    async def stop_pod(self, pod: str, *, grace_seconds: int | None = None) -> None:
        self._record("stop_pod", pod=pod, grace_seconds=grace_seconds)

    async def remove_pod(self, pod: str, *, force: bool = True) -> None:
        self._record("remove_pod", pod=pod, force=force)
        if self.remove_pod_error is not None:
            raise self.remove_pod_error

    async def list_pods(self, labels: Mapping[str, str]) -> list[ResourceSummary]:
        self._record("list_pods", labels=dict(labels))
        return self.pods

    async def create_container(self, spec: ContainerSpec) -> str:
        self._record("create_container", spec=spec)
        return f"ctr-{spec.name}"

    async def remove_container(self, container: str, *, force: bool = True) -> None:
        self._record("remove_container", container=container, force=force)

    async def list_containers(self, labels: Mapping[str, str]) -> list[ResourceSummary]:
        self._record("list_containers", labels=dict(labels))
        return self.containers

    async def exec_create(self, container: str, spec: ExecSpec) -> str:
        self._record("exec_create", container=container, spec=spec)
        return "exec-1"

    async def exec_start(self, exec_id: str) -> ExecOutput:
        self._record("exec_start", exec_id=exec_id)
        return self.exec_output

    async def exec_inspect(self, exec_id: str) -> ExecInspect:
        self._record("exec_inspect", exec_id=exec_id)
        return self.exec_result

    async def build_image(self, spec: BuildSpec) -> BuildResult:
        self._record("build_image", spec=spec)
        return self.build_result

    async def push_image(self, spec: PushSpec) -> PushResult:
        self._record("push_image", spec=spec)
        return self.push_result


# Assignable only if FakePodman structurally satisfies PodmanAPI, so the gate
# fails if the protocol and the fake drift apart.
_API: PodmanAPI = FakePodman()


@fixture
def data_dir() -> Generator[Path]:
    path = Path(tempfile.mkdtemp(prefix="danube-localrunner-"))
    yield path
    shutil.rmtree(path, ignore_errors=True)


@fixture
async def memory_db() -> AsyncGenerator[Database]:
    db = await open_database(":memory:")
    try:
        yield db
    finally:
        await db.close()


async def _seed_job(db: Database, job_id: str = "j1") -> None:
    """Seed team t1, pipeline p1, and `job_id` so runner_state FKs are satisfied."""
    async with db.transaction() as tx:
        await tx.execute(insert(Team(id="t1", name="team", global_admin=False)))
        await tx.execute(
            insert(
                Pipeline(
                    id="p1",
                    name="demo",
                    team_id="t1",
                    repo_url="https://example.test/repo.git",
                    worker_image="busybox:latest",
                )
            )
        )
        await tx.execute(
            insert(
                Job(
                    id=job_id,
                    pipeline_id="p1",
                    trigger_type=TriggerType.MANUAL,
                    status=JobStatus.SCHEDULING,
                )
            )
        )


def _pod_summary(job_id: str) -> ResourceSummary:
    return ResourceSummary(
        id=f"id-{job_id}",
        name=pod_name(job_id),
        labels={LABEL_JOB_ID: job_id, LABEL_MANAGED: "true"},
    )


async def _seed_reconcile(db: Database, data: Path) -> None:
    """Seed jobs, runner_state, and workspaces for the reconcile drift test."""
    statuses = {
        "run-ok": JobStatus.RUNNING,
        "fin-stale": JobStatus.SUCCESS,
        "run-missing": JobStatus.RUNNING,
        "clean-fail": JobStatus.FAILURE,
    }
    async with db.transaction() as tx:
        await tx.execute(insert(Team(id="t1", name="team", global_admin=False)))
        await tx.execute(
            insert(
                Pipeline(
                    id="p1",
                    name="demo",
                    team_id="t1",
                    repo_url="https://example.test/repo.git",
                    worker_image="busybox:latest",
                )
            )
        )
        for job_id, status in statuses.items():
            await tx.execute(
                insert(
                    Job(
                        id=job_id,
                        pipeline_id="p1",
                        trigger_type=TriggerType.MANUAL,
                        status=status,
                    )
                )
            )
        # run-ok and run-missing are tracked; clean-fail recorded a failed cleanup.
        for job_id in ("run-ok", "run-missing"):
            await tx.execute(
                insert(
                    RunnerState(
                        id=f"rs-{job_id}",
                        job_id=job_id,
                        kind="pod",
                        external_id=pod_name(job_id),
                        status="running",
                    )
                )
            )
        await tx.execute(
            insert(
                RunnerState(
                    id="rs-clean-fail",
                    job_id="clean-fail",
                    kind="pod",
                    external_id=pod_name("clean-fail"),
                    status="cleanup_failed",
                )
            )
        )
    # Workspaces on disk: one active (kept), one terminal (stale).
    for job_id in ("run-ok", "fin-stale"):
        (data / "workspaces" / job_id).mkdir(parents=True, exist_ok=True)


# Filesystem predicates live in sync helpers so the async tests can assert on
# disk state without tripping ruff ASYNC240 (blocking Path calls in async bodies);
# the temp dirs involved are tiny and local.
def _is_dir(path: Path) -> bool:
    return path.is_dir()


def _exists(path: Path) -> bool:
    return path.exists()


def _by_method(calls: list[Call], method: str) -> Call:
    matches = [call for call in calls if call.method == method]
    assert len(matches) == 1, f"expected exactly one {method!r} call, got {matches}"
    return matches[0]


def _spec(call: Call) -> ContainerSpec:
    spec = call.payload["spec"]
    assert isinstance(spec, ContainerSpec)
    return spec


# A typed reference asserting the runner satisfies the Runner protocol.
_RUNNER: Runner = LocalContainerRunner(FakePodman(), "danube-typecheck-only")


@test(mark="fast")
async def test_start_job_creates_pod_and_containers_with_labels() -> None:
    podman = FakePodman()
    data = load_fixture(data_dir())
    runner = LocalContainerRunner(podman, data)

    handle = await runner.start_job(START)

    name = pod_name("j1")
    assert_eq(handle.job_id, "j1")
    assert_eq(handle.pod_id, f"pod-{name}")
    assert_eq(handle.workspace_path, str(data / "workspaces" / "j1"))
    # Worker and coordinator created in the pod, then the pod started.
    assert_eq(
        podman.methods,
        [
            "image_exists",
            "pull_image",
            "image_exists",
            "pull_image",
            "ensure_network",
            "create_pod",
            "create_container",
            "create_container",
            "start_pod",
        ],
    )

    pod_spec = _by_method(podman.calls, "create_pod").payload["spec"]
    assert isinstance(pod_spec, PodSpec)
    assert_eq(pod_spec.name, name)
    assert_eq(
        dict(pod_spec.labels),
        {
            LABEL_JOB_ID: "j1",
            LABEL_PIPELINE_ID: "p1",
            LABEL_RESOURCE: "pod",
            LABEL_MANAGED: "true",
        },
    )
    # The workspace directory is created on the host.
    assert _is_dir(data / "workspaces" / "j1")


@test(mark="fast")
async def test_start_job_applies_security_defaults() -> None:
    podman = FakePodman()
    data = load_fixture(data_dir())
    runner = LocalContainerRunner(podman, data)

    _ = await runner.start_job(START)

    container_calls = [c for c in podman.calls if c.method == "create_container"]
    assert_eq(len(container_calls), 2)
    resources = {_spec(c).labels[LABEL_RESOURCE] for c in container_calls}
    assert_eq(resources, {"worker", "coordinator"})

    for call in container_calls:
        spec = _spec(call)
        assert spec.privileged is False, "containers must never be privileged"
        assert spec.read_only_rootfs is True, "root filesystem must be read-only"
        assert spec.no_new_privileges is True
        assert_eq(tuple(spec.cap_drop), ("ALL",))
        assert_eq(tuple(spec.cap_add), ())
        # Only the per-job workspace is mounted, writable, at /workspace.
        assert_eq(len(spec.mounts), 1)
        mount = spec.mounts[0]
        assert_eq(mount.destination, "/workspace")
        assert_eq(mount.source, str(data / "workspaces" / "j1"))
        assert mount.read_only is False
        # CPU, memory, and pids limits are all set.
        assert spec.limits is not None
        assert spec.limits.memory_bytes is not None
        assert spec.limits.pids_limit is not None
        assert spec.limits.cpu_quota is not None

    # Egress denied by default: the pod attaches to an internal network.
    network = _by_method(podman.calls, "ensure_network")
    assert_eq(network.payload["internal"], True)
    pod_spec = _by_method(podman.calls, "create_pod").payload["spec"]
    assert isinstance(pod_spec, PodSpec)
    assert_eq(tuple(pod_spec.networks), (network.payload["name"],))


@test(mark="fast")
async def test_start_job_container_body_carries_isolation_profile() -> None:
    # The unit half of the isolation verification: a job's container specs, once
    # translated to the libpod SpecGenerator body the adapter sends, carry the full
    # Isolation Profile with the runner's *resolved* default limits (not just "some
    # limit is set").
    podman = FakePodman()
    data = load_fixture(data_dir())
    runner = LocalContainerRunner(podman, data)

    _ = await runner.start_job(START)

    container_calls = [c for c in podman.calls if c.method == "create_container"]
    assert_eq(len(container_calls), 2)
    for call in container_calls:
        body = _container_body(_spec(call))
        assert_eq(body["privileged"], False)
        assert_eq(body["read_only_filesystem"], True)
        assert_eq(body["cap_drop"], ["ALL"])
        assert_eq(body["cap_add"], [])
        assert_eq(body["security_opt"], ["no-new-privileges"])
        # PID and IPC namespaces are explicitly private, never the host's.
        assert_eq(body["pidns"], {"nsmode": "private"})
        assert_eq(body["ipcns"], {"nsmode": "private"})
        # The resolved DEFAULT_LIMITS reach the wire body verbatim.
        assert_eq(
            body["resource_limits"],
            {
                "cpu": {
                    "quota": DEFAULT_LIMITS.cpu_quota,
                    "period": DEFAULT_LIMITS.cpu_period,
                },
                "memory": {"limit": DEFAULT_LIMITS.memory_bytes},
                "pids": {"limit": DEFAULT_LIMITS.pids_limit},
            },
        )

    # Egress denied by default: the pod attaches to the `internal` egress network,
    # which the runner ensures exists with `internal=True`.
    network = _by_method(podman.calls, "ensure_network")
    assert_eq(network.payload["internal"], True)
    assert_eq(network.payload["name"], DEFAULT_EGRESS_NETWORK)
    pod_spec = _by_method(podman.calls, "create_pod").payload["spec"]
    assert isinstance(pod_spec, PodSpec)
    assert_eq(tuple(pod_spec.networks), (DEFAULT_EGRESS_NETWORK,))


@test(mark="fast")
async def test_start_job_with_egress_attaches_outbound_network() -> None:
    # A pipeline that opts into egress (`egress: true`) puts the job pod on a
    # normal outbound network (`internal=False`) instead of the default-deny one.
    podman = FakePodman()
    data = load_fixture(data_dir())
    runner = LocalContainerRunner(podman, data)

    _ = await runner.start_job(
        StartJobRequest(
            job_id="j1", pipeline_id="p1", worker_image="busybox:latest", egress=True
        )
    )

    network = _by_method(podman.calls, "ensure_network")
    assert_eq(network.payload["internal"], False)
    assert_eq(network.payload["name"], DEFAULT_OUTBOUND_NETWORK)
    pod_spec = _by_method(podman.calls, "create_pod").payload["spec"]
    assert isinstance(pod_spec, PodSpec)
    assert_eq(tuple(pod_spec.networks), (DEFAULT_OUTBOUND_NETWORK,))


@test(mark="fast")
async def test_start_job_mounts_writable_scratch_under_ro_rootfs() -> None:
    # Both containers run with a read-only rootfs, so each must get the writable
    # tmpfs scratch (`/tmp`, `/run`, `/var/tmp`) real build tooling depends on.
    podman = FakePodman()
    runner = LocalContainerRunner(podman, load_fixture(data_dir()))

    _ = await runner.start_job(START)

    container_calls = [c for c in podman.calls if c.method == "create_container"]
    assert_eq(len(container_calls), 2)
    for call in container_calls:
        spec = _spec(call)
        assert spec.read_only_rootfs is True
        assert_eq(tuple(spec.tmpfs), SCRATCH_TMPFS)
        destinations = {t.destination for t in spec.tmpfs}
        assert_eq(destinations, {"/tmp", "/run", "/var/tmp"})
        # The wire body carries each scratch dir as a tmpfs mount alongside the
        # workspace bind mount, with the production hardening options (`nosuid`,
        # `nodev`; `noexec` deliberately omitted) reaching the body verbatim.
        body = _container_body(spec)
        tmpfs_mounts = [m for m in body["mounts"] if m["type"] == "tmpfs"]
        tmpfs_dests = {m["destination"] for m in tmpfs_mounts}
        assert_eq(tmpfs_dests, {"/tmp", "/run", "/var/tmp"})
        for mount in tmpfs_mounts:
            assert_eq(mount["options"], ["rw", "nosuid", "nodev"])


@test(mark="fast")
async def test_start_job_skips_pull_for_present_images() -> None:
    podman = FakePodman()
    podman.existing_images = {"busybox:latest", "localhost/danube-coordinator:latest"}
    runner = LocalContainerRunner(podman, load_fixture(data_dir()))

    _ = await runner.start_job(START)

    assert "pull_image" not in podman.methods


@test(mark="fast")
async def test_exec_step_execs_in_worker_and_maps_result() -> None:
    podman = FakePodman()
    podman.exec_output = ExecOutput(stdout="hello\n", stderr="warn\n")
    podman.exec_result = ExecInspect(exit_code=0, running=False)
    runner = LocalContainerRunner(podman, load_fixture(data_dir()))

    handle = JobHandle(job_id="j1", pod_id="pod-x", workspace_path="/ws")
    result = await runner.exec_step(handle, ExecStepRequest(command="echo hello"))

    assert_eq(result.exit_code, 0)
    assert_eq(result.stdout, "hello\n")
    assert_eq(result.stderr, "warn\n")

    exec_call = _by_method(podman.calls, "exec_create")
    assert_eq(exec_call.payload["container"], f"{pod_name('j1')}-worker")
    spec = exec_call.payload["spec"]
    assert isinstance(spec, ExecSpec)
    assert_eq(tuple(spec.command), ("/bin/sh", "-c", "echo hello"))
    assert_eq(spec.work_dir, "/workspace")
    # Exec sequence: create, start (stream), inspect (exit code).
    assert_eq(podman.methods, ["exec_create", "exec_start", "exec_inspect"])


@test(mark="fast")
async def test_exec_step_propagates_nonzero_exit_code() -> None:
    podman = FakePodman()
    podman.exec_output = ExecOutput(stdout="", stderr="boom")
    podman.exec_result = ExecInspect(exit_code=2, running=False)
    runner = LocalContainerRunner(podman, load_fixture(data_dir()))

    handle = JobHandle(job_id="j1", pod_id="pod-x", workspace_path="/ws")
    result = await runner.exec_step(handle, ExecStepRequest(command="false"))

    assert_eq(result.exit_code, 2)
    assert_eq(result.stderr, "boom")


@test(mark="fast")
async def test_build_image_drives_host_podman_build() -> None:
    podman = FakePodman()
    podman.build_result = BuildResult(
        success=True, image_id="sha256:built", output="STEP 1/1\n"
    )
    runner = LocalContainerRunner(podman, load_fixture(data_dir()))

    handle = JobHandle(job_id="j1", pod_id="pod-x", workspace_path="/ws")
    result = await runner.build_image(
        handle,
        BuildImageRequest(
            tag="app:abc", context_path="/ws/build", dockerfile="Dockerfile"
        ),
    )

    assert result.success is True
    assert_eq(result.image_id, "sha256:built")
    assert_eq(result.tag, "app:abc")
    assert_eq(result.output, "STEP 1/1\n")
    # The build runs on host Podman (no pod/exec), with network disabled for RUN.
    assert_eq(podman.methods, ["build_image"])
    spec = _by_method(podman.calls, "build_image").payload["spec"]
    assert isinstance(spec, BuildSpec)
    assert_eq(spec.context_path, "/ws/build")
    assert_eq(spec.tag, "app:abc")
    assert_eq(spec.dockerfile, "Dockerfile")
    assert_eq(spec.network, False)
    assert_eq(dict(spec.build_args), {})
    assert_eq(spec.target, None)


@test(mark="fast")
async def test_build_image_threads_build_args_network_and_target() -> None:
    podman = FakePodman()
    podman.build_result = BuildResult(success=True, image_id="sha256:built", output="")
    runner = LocalContainerRunner(podman, load_fixture(data_dir()))

    handle = JobHandle(job_id="j1", pod_id="pod-x", workspace_path="/ws")
    _ = await runner.build_image(
        handle,
        BuildImageRequest(
            tag="app:abc",
            context_path="/ws/build",
            build_args={"VERSION": "1.2.3"},
            network=True,
            target="runtime",
        ),
    )

    spec = _by_method(podman.calls, "build_image").payload["spec"]
    assert isinstance(spec, BuildSpec)
    assert_eq(dict(spec.build_args), {"VERSION": "1.2.3"})
    assert_eq(spec.network, True)
    assert_eq(spec.target, "runtime")


@test(mark="fast")
async def test_build_image_reports_failure() -> None:
    podman = FakePodman()
    podman.build_result = BuildResult(
        success=False, image_id="", output="error building\n"
    )
    runner = LocalContainerRunner(podman, load_fixture(data_dir()))

    handle = JobHandle(job_id="j1", pod_id="pod-x", workspace_path="/ws")
    result = await runner.build_image(
        handle, BuildImageRequest(tag="app:abc", context_path="/ws")
    )

    assert result.success is False
    assert_eq(result.image_id, "")


@test(mark="fast")
async def test_push_image_drives_host_podman_push() -> None:
    podman = FakePodman()
    podman.push_result = PushResult(
        success=True, digest="sha256:pushed", output="Writing manifest\n"
    )
    runner = LocalContainerRunner(podman, load_fixture(data_dir()))

    handle = JobHandle(job_id="j1", pod_id="pod-x", workspace_path="/ws")
    result = await runner.push_image(
        handle,
        PushImageRequest(
            tag="app:abc",
            registry="registry.example.com",
            credentials=RegistryCredentials(username="ci", password="pw"),
        ),
    )

    assert result.success is True
    # The push runs on host Podman (no pod/exec) to the composed destination.
    assert_eq(podman.methods, ["push_image"])
    assert_eq(result.reference, "registry.example.com/app:abc")
    assert_eq(result.digest, "sha256:pushed")
    spec = _by_method(podman.calls, "push_image").payload["spec"]
    assert isinstance(spec, PushSpec)
    assert_eq(spec.source, "app:abc")
    assert_eq(spec.destination, "registry.example.com/app:abc")
    assert_eq(spec.tls_verify, True)
    assert spec.auth is not None
    assert_eq(spec.auth.username, "ci")
    assert_eq(spec.auth.password, "pw")


@test(mark="fast")
async def test_push_image_without_credentials_sends_no_auth() -> None:
    podman = FakePodman()
    runner = LocalContainerRunner(podman, load_fixture(data_dir()))

    handle = JobHandle(job_id="j1", pod_id="pod-x", workspace_path="/ws")
    _ = await runner.push_image(
        handle,
        PushImageRequest(tag="app:abc", registry="localhost:5000", tls_verify=False),
    )

    spec = _by_method(podman.calls, "push_image").payload["spec"]
    assert isinstance(spec, PushSpec)
    assert spec.auth is None
    assert_eq(spec.tls_verify, False)


@test(mark="fast")
async def test_push_image_reports_failure() -> None:
    podman = FakePodman()
    podman.push_result = PushResult(
        success=False, digest="", output="unauthorized: authentication required\n"
    )
    runner = LocalContainerRunner(podman, load_fixture(data_dir()))

    handle = JobHandle(job_id="j1", pod_id="pod-x", workspace_path="/ws")
    result = await runner.push_image(
        handle, PushImageRequest(tag="app:abc", registry="registry.example.com")
    )

    assert result.success is False
    assert_eq(result.digest, "")


@test(mark="fast")
async def test_start_and_wait_for_coordinator_execs_entrypoint() -> None:
    podman = FakePodman()
    podman.exec_output = ExecOutput(stdout="coordinator log\n", stderr="")
    podman.exec_result = ExecInspect(exit_code=0, running=False)
    runner = LocalContainerRunner(podman, load_fixture(data_dir()))
    handle = JobHandle(job_id="j1", pod_id="pod-x", workspace_path="/ws")

    await runner.start_coordinator(
        handle, {"DANUBE_JOB_ID": "j1", "DANUBE_RPC_TOKEN": "tok"}
    )
    exit_info = await runner.wait_for_coordinator(handle)

    assert_eq(exit_info.exit_code, 0)
    assert_eq(exit_info.stdout, "coordinator log\n")
    # The entrypoint is exec'd in the Coordinator container, not the Worker.
    exec_create = _by_method(podman.calls, "exec_create")
    assert_eq(exec_create.payload["container"], f"{pod_name('j1')}-coordinator")
    spec = exec_create.payload["spec"]
    assert isinstance(spec, ExecSpec)
    assert_eq(tuple(spec.command), ("python", "-m", "danube.coordinator"))
    assert_eq(spec.work_dir, "/workspace")
    assert_eq(spec.env["DANUBE_RPC_TOKEN"], "tok")
    assert_eq(podman.methods, ["exec_create", "exec_start", "exec_inspect"])


@test(mark="fast")
async def test_wait_for_coordinator_propagates_nonzero_exit() -> None:
    podman = FakePodman()
    podman.exec_result = ExecInspect(exit_code=1, running=False)
    runner = LocalContainerRunner(podman, load_fixture(data_dir()))
    handle = JobHandle(job_id="j1", pod_id="pod-x", workspace_path="/ws")

    await runner.start_coordinator(handle, {})
    exit_info = await runner.wait_for_coordinator(handle)

    assert_eq(exit_info.exit_code, 1)


@test(mark="fast")
async def test_wait_for_coordinator_without_start_raises() -> None:
    runner = LocalContainerRunner(FakePodman(), load_fixture(data_dir()))
    handle = JobHandle(job_id="j1", pod_id="pod-x", workspace_path="/ws")

    with assert_raises(CoordinatorNotStartedError):
        _ = await runner.wait_for_coordinator(handle)


@test(mark="fast")
async def test_coordinator_env_merges_config_defaults() -> None:
    podman = FakePodman()
    config = LocalRunnerConfig(coordinator_env={"PYTHONPATH": "/opt/danube"})
    runner = LocalContainerRunner(podman, load_fixture(data_dir()), config=config)
    handle = JobHandle(job_id="j1", pod_id="pod-x", workspace_path="/ws")

    # The connection env overrides config defaults on key collisions.
    await runner.start_coordinator(handle, {"DANUBE_JOB_ID": "j1"})

    spec = _by_method(podman.calls, "exec_create").payload["spec"]
    assert isinstance(spec, ExecSpec)
    assert_eq(spec.env["PYTHONPATH"], "/opt/danube")
    assert_eq(spec.env["DANUBE_JOB_ID"], "j1")


@test(mark="fast")
async def test_coordinator_mounts_apply_only_to_coordinator() -> None:
    podman = FakePodman()
    package_mount = Mount(
        source="/src/danube", destination="/opt/danube", read_only=True
    )
    config = LocalRunnerConfig(coordinator_mounts=(package_mount,))
    runner = LocalContainerRunner(podman, load_fixture(data_dir()), config=config)

    _ = await runner.start_job(START)

    by_resource = {
        _spec(c).labels[LABEL_RESOURCE]: _spec(c)
        for c in podman.calls
        if c.method == "create_container"
    }
    worker_destinations = {m.destination for m in by_resource[RESOURCE_WORKER].mounts}
    coordinator_destinations = {
        m.destination for m in by_resource[RESOURCE_COORDINATOR].mounts
    }
    assert "/opt/danube" not in worker_destinations
    assert "/opt/danube" in coordinator_destinations


@test(mark="fast")
async def test_cleanup_removes_pod_and_workspace() -> None:
    podman = FakePodman()
    data = load_fixture(data_dir())
    runner = LocalContainerRunner(podman, data)
    handle = await runner.start_job(START)
    workspace = Path(handle.workspace_path)
    assert _is_dir(workspace)

    await runner.cleanup_job(handle)

    remove = _by_method(podman.calls, "remove_pod")
    assert_eq(remove.payload["pod"], pod_name("j1"))
    assert_eq(remove.payload["force"], True)
    assert not _exists(workspace), "workspace must be deleted on cleanup"


@test(mark="fast")
async def test_cleanup_is_idempotent_when_pod_already_gone() -> None:
    podman = FakePodman()
    runner = LocalContainerRunner(podman, load_fixture(data_dir()))
    handle = JobHandle(job_id="ghost", pod_id="p", workspace_path="/ws")

    # The adapter swallows a missing pod, so cleanup must not raise.
    await runner.cleanup_job(handle)
    await runner.cleanup_job(handle)

    assert_eq([c.method for c in podman.calls], ["remove_pod", "remove_pod"])


@test(mark="fast")
async def test_stop_job_stops_pod() -> None:
    podman = FakePodman()
    runner = LocalContainerRunner(podman, load_fixture(data_dir()))
    handle = JobHandle(job_id="j1", pod_id="p", workspace_path="/ws")

    await runner.stop_job(handle, reason="timeout")

    stop = _by_method(podman.calls, "stop_pod")
    assert_eq(stop.payload["pod"], pod_name("j1"))


@test(mark="fast")
async def test_health_reports_runtime_version() -> None:
    podman = FakePodman()
    runner = LocalContainerRunner(podman, load_fixture(data_dir()))

    health = await runner.health()

    assert health.healthy is True
    assert_eq(health.runtime, "podman")
    assert_eq(health.version, "5.0.0")


@test(mark="fast")
async def test_health_reports_unreachable_socket() -> None:
    podman = FakePodman()
    podman.alive = False
    runner = LocalContainerRunner(podman, load_fixture(data_dir()))

    health = await runner.health()

    assert health.healthy is False
    assert "version" not in podman.methods


@test(mark="fast")
async def test_start_job_records_runner_state() -> None:
    podman = FakePodman()
    db = await load_fixture(memory_db())
    await _seed_job(db)
    runner = LocalContainerRunner(podman, load_fixture(data_dir()), db=db)

    _ = await runner.start_job(START)

    async with db.transaction() as tx:
        rows = await tx.fetch_all(
            select(RunnerState).where(RunnerState.job_id.eq("j1"))
        )
    kinds = {row.kind for row in rows}
    assert_eq(kinds, {"pod", "worker", "coordinator"})
    assert all(row.status == "running" for row in rows)


@test(mark="fast")
async def test_cleanup_clears_runner_state() -> None:
    podman = FakePodman()
    db = await load_fixture(memory_db())
    await _seed_job(db)
    runner = LocalContainerRunner(podman, load_fixture(data_dir()), db=db)
    handle = await runner.start_job(START)

    await runner.cleanup_job(handle)

    async with db.transaction() as tx:
        rows = await tx.fetch_all(
            select(RunnerState).where(RunnerState.job_id.eq("j1"))
        )
    assert_eq(rows, [])


@test(mark="fast")
async def test_cleanup_failure_records_state_and_reraises() -> None:
    podman = FakePodman()
    podman.remove_pod_error = PodmanError("DELETE", "/pods/x", 500, "boom")
    db = await load_fixture(memory_db())
    await _seed_job(db)
    runner = LocalContainerRunner(podman, load_fixture(data_dir()), db=db)
    handle = await runner.start_job(START)

    with assert_raises(PodmanError):
        await runner.cleanup_job(handle)

    async with db.transaction() as tx:
        rows = await tx.fetch_all(
            select(RunnerState.status).where(RunnerState.job_id.eq("j1"))
        )
    assert "cleanup_failed" in rows


@test(mark="fast")
async def test_reconcile_without_db_raises() -> None:
    runner = LocalContainerRunner(FakePodman(), load_fixture(data_dir()))
    with assert_raises(ReconcileWithoutDatabaseError):
        _ = await runner.reconcile()


@test(mark="fast")
async def test_reconcile_classifies_drift() -> None:
    podman = FakePodman()
    data = load_fixture(data_dir())
    db = await load_fixture(memory_db())
    await _seed_reconcile(db, data)
    runner = LocalContainerRunner(podman, data, db=db)

    # Live Podman resources: a pod for the still-running job, a pod for a
    # finished job (stale), a pod whose job_id has no `Job` row (also stale), and
    # a container for a job with no tracked state.
    podman.pods = [
        _pod_summary("run-ok"),
        _pod_summary("fin-stale"),
        _pod_summary("ghost"),
    ]
    podman.containers = [
        ResourceSummary(
            id="c-orphan",
            name="danube-job-orphan-worker",
            labels={LABEL_JOB_ID: "orphan", LABEL_MANAGED: "true"},
        )
    ]

    report = await runner.reconcile()

    assert_eq(report.stale_pods, [pod_name("fin-stale"), pod_name("ghost")])
    assert_eq(report.orphaned_containers, ["danube-job-orphan-worker"])
    assert_eq(report.missing_pods, ["run-missing"])
    assert_eq(report.stale_workspaces, ["fin-stale"])
    assert_eq(report.failed_cleanups, ["clean-fail"])
