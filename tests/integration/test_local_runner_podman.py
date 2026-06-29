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
from pathlib import Path

import httpx
from snektest import assert_eq, fixture, load_fixture, test

from danube.domain.runner_types import (
    BuildImageRequest,
    ExecStepRequest,
    JobHandle,
    StartJobRequest,
)
from danube.runner.local import LABEL_JOB_ID, LocalContainerRunner, LocalRunnerConfig
from danube.runner.podman import PodmanAdapter, build_async_client, socket_available

# Fully qualified so rootless Podman does not prompt for a registry.
BUSYBOX_IMAGE = "docker.io/library/busybox:latest"


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

    @test(mark="slow")
    async def test_health_reports_healthy_against_live_socket() -> None:
        client = await load_fixture(podman_client())
        data = load_fixture(data_dir())
        runner = LocalContainerRunner(PodmanAdapter(client), data)

        health = await runner.health()

        assert health.healthy is True
        assert_eq(health.runtime, "podman")
