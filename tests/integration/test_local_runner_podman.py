"""Integration tests for `LocalContainerRunner` against real rootless Podman.

These talk to a live Podman API socket, so they only register when a socket is
present (`socket_available()`); on a host without rootless Podman the module
defines no tests and the suite simply skips them, which the issue's validation
gate accepts.

What they prove (the "first spike" from `docs/architecture/local-runner.md`):
a pod with a Worker + Coordinator is created, a command execs in the Worker and
returns its stdout and exit code, direct internet egress from the Worker is
denied by default, and cleanup removes every labelled resource.
"""

import shutil
import tempfile
import uuid
from collections.abc import AsyncGenerator, Generator
from pathlib import Path

import httpx
from snektest import assert_eq, fixture, load_fixture, test

from danube.domain.runner_types import ExecStepRequest, StartJobRequest
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


# Only register the socket-dependent tests when a Podman socket is actually
# present; otherwise the module contributes no tests and the run skips them.
if socket_available():

    @test(mark="slow")
    async def test_first_spike_exec_egress_and_cleanup() -> None:
        client = await load_fixture(podman_client())
        data = load_fixture(data_dir())
        adapter = PodmanAdapter(client)
        # Use BusyBox for the Coordinator too so the test needs only one image.
        runner = LocalContainerRunner(
            adapter,
            data,
            config=LocalRunnerConfig(coordinator_image=BUSYBOX_IMAGE),
        )

        job_id = f"it-{uuid.uuid4().hex[:12]}"
        request = StartJobRequest(
            job_id=job_id, pipeline_id="int", worker_image=BUSYBOX_IMAGE
        )

        handle = await runner.start_job(request)
        try:
            result = await runner.exec_step(
                handle, ExecStepRequest(command="echo hello")
            )
            assert_eq(result.exit_code, 0)
            assert "hello" in result.stdout

            # Default-deny egress: a direct connection to the internet must fail.
            egress = await runner.exec_step(
                handle,
                ExecStepRequest(
                    command="wget -T 3 -q -O /dev/null http://1.1.1.1",
                ),
            )
            assert egress.exit_code != 0, "worker reached the internet directly"
        finally:
            await runner.cleanup_job(handle)

        # Every labelled resource for the job is gone after cleanup.
        pods = await adapter.list_pods({LABEL_JOB_ID: job_id})
        assert_eq(pods, [])
        containers = await adapter.list_containers({LABEL_JOB_ID: job_id})
        assert_eq(containers, [])
        assert not _exists(data / "workspaces" / job_id)

    @test(mark="slow")
    async def test_health_reports_healthy_against_live_socket() -> None:
        client = await load_fixture(podman_client())
        data = load_fixture(data_dir())
        runner = LocalContainerRunner(PodmanAdapter(client), data)

        health = await runner.health()

        assert health.healthy is True
        assert_eq(health.runtime, "podman")
