"""Integration tests for `LocalContainerRunner` against real rootless Podman.

These talk to a live Podman API socket, so they only register when a socket is
present (`socket_available()`); on a host without rootless Podman the module
defines no tests and the suite simply skips them, which the validation gate
accepts.

What they prove (the "first spike" from `docs/architecture/local-runner.md`),
one behaviour per test: a command execs in the Worker and returns its stdout and
exit code; direct internet egress from the Worker is denied by default; and
cleanup removes every labelled resource.
"""

import shutil
import tempfile
import uuid
from collections.abc import AsyncGenerator, Generator
from contextlib import asynccontextmanager
from pathlib import Path

import anyio
import httpx
from snektest import assert_eq, fixture, load_fixture, test

from danube.domain.runner_types import (
    BuildImageRequest,
    ExecStepRequest,
    JobHandle,
    PushImageRequest,
    StartJobRequest,
)
from danube.runner.local import LABEL_JOB_ID, LocalContainerRunner, LocalRunnerConfig
from danube.runner.podman import (
    DEFAULT_API_VERSION,
    PodmanAdapter,
    build_async_client,
    socket_available,
)

# Fully qualified so rootless Podman does not prompt for a registry.
BUSYBOX_IMAGE = "docker.io/library/busybox:latest"
# A throwaway registry for the push round-trip; runs on the host network so the
# host Podman that does the push can reach it at 127.0.0.1:<REGISTRY_PORT>.
REGISTRY_IMAGE = "docker.io/library/registry:2"
REGISTRY_PORT = 5000


@fixture
async def podman_client() -> AsyncGenerator[httpx.AsyncClient]:
    client = build_async_client()
    try:
        yield client
    finally:
        await client.aclose()


@fixture
def data_dir() -> Generator[Path]:
    path = Path(tempfile.mkdtemp(prefix="danube-podman-int-"))
    yield path
    shutil.rmtree(path, ignore_errors=True)


def _exists(path: Path) -> bool:
    return path.exists()


def _make_runner(client: httpx.AsyncClient, data: Path) -> LocalContainerRunner:
    # BusyBox for the Coordinator too, so the tests need only one image.
    return LocalContainerRunner(
        PodmanAdapter(client),
        data,
        config=LocalRunnerConfig(coordinator_image=BUSYBOX_IMAGE),
    )


def _job_request(job_id: str) -> StartJobRequest:
    return StartJobRequest(job_id=job_id, pipeline_id="int", worker_image=BUSYBOX_IMAGE)


# Manifest media types so the registry returns a v2/OCI manifest (whose digest is
# the one a `push` reports) rather than a converted schema-1 manifest.
_MANIFEST_ACCEPT = (
    "application/vnd.oci.image.manifest.v1+json,"
    "application/vnd.docker.distribution.manifest.v2+json"
)


@asynccontextmanager
async def _throwaway_registry(
    client: httpx.AsyncClient,
) -> AsyncGenerator[str]:
    """Run a throwaway `registry:2` container on the host network and yield its
    ``host:port``. Reachable from the host Podman that performs the push, so the
    round-trip needs no insecure-registry pull support in the adapter."""
    adapter = PodmanAdapter(client)
    await adapter.pull_image(REGISTRY_IMAGE)
    name = f"danube-it-registry-{uuid.uuid4().hex[:12]}"
    create = await client.post(
        f"/{DEFAULT_API_VERSION}/libpod/containers/create",
        json={"image": REGISTRY_IMAGE, "name": name, "netns": {"nsmode": "host"}},
    )
    create.raise_for_status()
    container_id = str(create.json()["Id"])
    try:
        start = await client.post(
            f"/{DEFAULT_API_VERSION}/libpod/containers/{container_id}/start"
        )
        start.raise_for_status()
        await _await_registry_ready()
        yield f"127.0.0.1:{REGISTRY_PORT}"
    finally:
        await adapter.remove_container(container_id, force=True)


async def _await_registry_ready() -> None:
    """Poll the registry's `/v2/` endpoint until it answers, or give up."""
    deadline = 30.0
    async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{REGISTRY_PORT}") as probe:
        for _ in range(int(deadline / 0.5)):
            try:
                response = await probe.get("/v2/")
            except httpx.HTTPError:
                await anyio.sleep(0.5)
                continue
            if response.status_code in (200, 401):
                return
            await anyio.sleep(0.5)
    msg = "throwaway registry did not become ready"
    raise RuntimeError(msg)


async def _registry_manifest_digest(repository: str, tag: str) -> str:
    """Return the registry's `Docker-Content-Digest` for ``repository:tag``."""
    async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{REGISTRY_PORT}") as probe:
        response = await probe.get(
            f"/v2/{repository}/manifests/{tag}",
            headers={"Accept": _MANIFEST_ACCEPT},
        )
    response.raise_for_status()
    return response.headers["Docker-Content-Digest"]


# Only register the socket-dependent tests when a Podman socket is actually
# present; otherwise the module contributes no tests and the run skips them.
if socket_available():

    @test(mark="slow")
    async def test_exec_step_runs_command_in_worker() -> None:
        client = await load_fixture(podman_client())
        data = load_fixture(data_dir())
        runner = _make_runner(client, data)
        handle = await runner.start_job(_job_request(f"it-{uuid.uuid4().hex[:12]}"))
        try:
            result = await runner.exec_step(
                handle, ExecStepRequest(command="echo hello")
            )
            assert_eq(result.exit_code, 0)
            assert "hello" in result.stdout
        finally:
            await runner.cleanup_job(handle)

    @test(mark="slow")
    async def test_worker_egress_denied_by_default() -> None:
        client = await load_fixture(podman_client())
        data = load_fixture(data_dir())
        runner = _make_runner(client, data)
        handle = await runner.start_job(_job_request(f"it-{uuid.uuid4().hex[:12]}"))
        try:
            # Default-deny egress: a direct connection to the internet must fail.
            egress = await runner.exec_step(
                handle,
                ExecStepRequest(command="wget -T 3 -q -O /dev/null http://1.1.1.1"),
            )
            assert egress.exit_code != 0, "worker reached the internet directly"
        finally:
            await runner.cleanup_job(handle)

    @test(mark="slow")
    async def test_cleanup_removes_all_labeled_resources() -> None:
        client = await load_fixture(podman_client())
        data = load_fixture(data_dir())
        adapter = PodmanAdapter(client)
        runner = LocalContainerRunner(
            adapter, data, config=LocalRunnerConfig(coordinator_image=BUSYBOX_IMAGE)
        )
        job_id = f"it-{uuid.uuid4().hex[:12]}"
        handle = await runner.start_job(_job_request(job_id))

        await runner.cleanup_job(handle)

        pods = await adapter.list_pods({LABEL_JOB_ID: job_id})
        assert_eq(pods, [])
        containers = await adapter.list_containers({LABEL_JOB_ID: job_id})
        assert_eq(containers, [])
        assert not _exists(data / "workspaces" / job_id)

    @test(mark="slow")
    async def test_build_image_produces_tagged_image_in_store() -> None:
        client = await load_fixture(podman_client())
        data = load_fixture(data_dir())
        runner = _make_runner(client, data)
        adapter = PodmanAdapter(client)
        context = data / "ctx"
        context.mkdir(parents=True)
        # A trivial build with no RUN instructions, so it succeeds even with the
        # default network=none and without pulling any base image.
        (context / "Dockerfile").write_text(
            "FROM scratch\nCOPY Dockerfile /Dockerfile\n"
        )
        tag = f"localhost/danube-it-{uuid.uuid4().hex[:12]}:latest"
        handle = JobHandle(job_id="build-it", pod_id="none", workspace_path=str(data))

        result = await runner.build_image(
            handle, BuildImageRequest(tag=tag, context_path=str(context))
        )

        assert result.success is True, result.output
        assert result.image_id != ""
        # The image is now in the host Local Image Store.
        assert await adapter.image_exists(tag)

    @test(mark="slow")
    async def test_build_image_build_arg_flows_into_run() -> None:
        client = await load_fixture(podman_client())
        data = load_fixture(data_dir())
        runner = _make_runner(client, data)
        context = data / "ctx-args"
        context.mkdir(parents=True)
        # `ARG` is consumed by a `RUN echo`, which needs no network, so the value
        # surfaces in the build output proving `build_args` reached the build.
        (context / "Dockerfile").write_text(
            f'FROM {BUSYBOX_IMAGE}\nARG GREETING\nRUN echo "GREETING=$GREETING"\n'
        )
        tag = f"localhost/danube-it-{uuid.uuid4().hex[:12]}:latest"
        handle = JobHandle(job_id="build-args", pod_id="none", workspace_path=str(data))

        result = await runner.build_image(
            handle,
            BuildImageRequest(
                tag=tag,
                context_path=str(context),
                build_args={"GREETING": "hello-from-arg"},
            ),
        )

        assert result.success is True, result.output
        assert "GREETING=hello-from-arg" in result.output

    @test(mark="slow")
    async def test_build_run_egress_denied_by_default() -> None:
        client = await load_fixture(podman_client())
        data = load_fixture(data_dir())
        runner = _make_runner(client, data)
        context = data / "ctx-no-net"
        context.mkdir(parents=True)
        # A `RUN` that reaches the internet must fail under the default deny.
        (context / "Dockerfile").write_text(
            f"FROM {BUSYBOX_IMAGE}\nRUN wget -T 3 -q -O /dev/null http://1.1.1.1\n"
        )
        tag = f"localhost/danube-it-{uuid.uuid4().hex[:12]}:latest"
        handle = JobHandle(
            job_id="build-nonet", pod_id="none", workspace_path=str(data)
        )

        result = await runner.build_image(
            handle, BuildImageRequest(tag=tag, context_path=str(context))
        )

        assert result.success is False, "RUN reached the internet under default deny"

    @test(mark="slow")
    async def test_build_run_egress_allowed_with_network_opt_in() -> None:
        client = await load_fixture(podman_client())
        data = load_fixture(data_dir())
        runner = _make_runner(client, data)
        context = data / "ctx-net"
        context.mkdir(parents=True)
        (context / "Dockerfile").write_text(
            f"FROM {BUSYBOX_IMAGE}\nRUN wget -T 5 -q -O /dev/null http://1.1.1.1\n"
        )
        tag = f"localhost/danube-it-{uuid.uuid4().hex[:12]}:latest"
        handle = JobHandle(job_id="build-net", pod_id="none", workspace_path=str(data))

        result = await runner.build_image(
            handle,
            BuildImageRequest(tag=tag, context_path=str(context), network=True),
        )

        assert result.success is True, result.output

    @test(mark="slow")
    async def test_build_image_target_selects_stage() -> None:
        client = await load_fixture(podman_client())
        data = load_fixture(data_dir())
        runner = _make_runner(client, data)
        context = data / "ctx-target"
        context.mkdir(parents=True)
        # The `build` stage would fail (its RUN needs the network), so a successful
        # build proves `target` stopped at the earlier `base` stage.
        stages = [
            f"FROM {BUSYBOX_IMAGE} AS base",
            "RUN echo base-stage",
            "FROM base AS build",
            "RUN wget -T 3 -q -O /dev/null http://1.1.1.1",
        ]
        (context / "Dockerfile").write_text("\n".join(stages) + "\n")
        tag = f"localhost/danube-it-{uuid.uuid4().hex[:12]}:latest"
        handle = JobHandle(job_id="build-tgt", pod_id="none", workspace_path=str(data))

        result = await runner.build_image(
            handle,
            BuildImageRequest(tag=tag, context_path=str(context), target="base"),
        )

        assert result.success is True, result.output

    def _trivial_image(data: Path, name: str) -> tuple[str, Path]:
        """Lay out a context that builds a trivial pushable image, returning the
        build tag and the context path."""
        context = data / name
        context.mkdir(parents=True)
        (context / "Dockerfile").write_text(
            "FROM scratch\nCOPY Dockerfile /Dockerfile\n"
        )
        return f"{name}:latest", context

    @test(mark="slow")
    async def test_push_round_trip_to_throwaway_registry() -> None:
        client = await load_fixture(podman_client())
        data = load_fixture(data_dir())
        runner = _make_runner(client, data)
        repository = f"danube-it-{uuid.uuid4().hex[:12]}"
        tag, context = _trivial_image(data, repository)
        handle = JobHandle(job_id="push-it", pod_id="none", workspace_path=str(data))
        built = await runner.build_image(
            handle, BuildImageRequest(tag=tag, context_path=str(context))
        )
        assert built.success is True, built.output

        async with _throwaway_registry(client) as registry:
            result = await runner.push_image(
                handle,
                PushImageRequest(tag=tag, registry=registry, tls_verify=False),
            )

            assert result.success is True, result.output
            assert result.digest.startswith("sha256:"), result.output
            # Pulling the manifest back yields the same digest the push reported.
            registry_digest = await _registry_manifest_digest(repository, "latest")
            assert_eq(registry_digest, result.digest)

    @test(mark="slow")
    async def test_push_to_unreachable_registry_is_a_failure() -> None:
        client = await load_fixture(podman_client())
        data = load_fixture(data_dir())
        runner = _make_runner(client, data)
        repository = f"danube-it-{uuid.uuid4().hex[:12]}"
        tag, context = _trivial_image(data, repository)
        handle = JobHandle(job_id="push-bad", pod_id="none", workspace_path=str(data))
        built = await runner.build_image(
            handle, BuildImageRequest(tag=tag, context_path=str(context))
        )
        assert built.success is True, built.output

        # No registry is listening here, so the push cannot complete: a failed push
        # is a step failure, not an exception.
        result = await runner.push_image(
            handle,
            PushImageRequest(tag=tag, registry="127.0.0.1:5999", tls_verify=False),
        )

        assert result.success is False, result.output

    @test(mark="slow")
    async def test_health_reports_healthy_against_live_socket() -> None:
        client = await load_fixture(podman_client())
        data = load_fixture(data_dir())
        runner = LocalContainerRunner(PodmanAdapter(client), data)

        health = await runner.health()

        assert health.healthy is True
        assert_eq(health.runtime, "podman")
