"""Behavioral verification of the Isolation Profile on real rootless Podman (#50).

The unit half (`tests/unit/test_local_runner.py`,
`test_start_job_container_body_carries_isolation_profile`) asserts the runner
*requests* the profile in the libpod body. This is the other half: it boots a real
job pod with `LocalContainerRunner`'s production defaults and execs into the Worker
to prove the kernel actually *enforces* the profile — a read-only rootfs rejects
writes outside `/workspace`, all Linux capabilities are dropped, and the
default-deny `internal` network blocks egress.

It registers only when a Podman socket is present, so it skips on hosts without
rootless Podman (the validation gate accepts that). This is also the seed of the
shared real-Podman test harness referenced by the wider verification theme.

NOTE: this environment has no Podman socket, so this test is UNVERIFIED here. It is
written to the spec above and may need adjustment on a real rootless-Podman host
(notably the BusyBox applet behaviour for `wget`/`grep` and the exact failure mode
of an outbound connection on an `internal` network).
"""

import shutil
import tempfile
from collections.abc import AsyncGenerator
from pathlib import Path

import httpx
from snektest import assert_eq, fixture, load_fixture, test

from danube.domain.runner_types import ExecStepRequest, StartJobRequest
from danube.runner.local import LocalContainerRunner, LocalRunnerConfig
from danube.runner.podman import PodmanAdapter, build_async_client, socket_available

# A tiny, ubiquitous image with the `sh`/`grep`/`wget` applets the checks below use.
WORKER_IMAGE = "docker.io/library/busybox:latest"


if socket_available():

    @fixture
    async def podman_client() -> AsyncGenerator[httpx.AsyncClient]:
        client = build_async_client()
        try:
            yield client
        finally:
            await client.aclose()

    @test(mark="slow")
    async def test_isolation_profile_enforced_on_real_podman() -> None:
        client = await load_fixture(podman_client())
        adapter = PodmanAdapter(client)
        data_dir = Path(tempfile.mkdtemp(prefix="danube-isolation-"))
        # Reuse the Worker image for the Coordinator so `start_job`'s image-ensure
        # step does not try to pull the (absent) default Coordinator image; this
        # test only execs in the Worker and never starts the Coordinator pipeline.
        config = LocalRunnerConfig(coordinator_image=WORKER_IMAGE)
        runner = LocalContainerRunner(adapter, data_dir, config=config)
        request = StartJobRequest(
            job_id="iso-1", pipeline_id="p1", worker_image=WORKER_IMAGE
        )

        handle = await runner.start_job(request)
        try:
            # Read-only rootfs: a write outside the workspace is rejected ...
            outside = await runner.exec_step(
                handle, ExecStepRequest(command="touch /should-not-write")
            )
            assert outside.exit_code != 0, (
                "RO rootfs must reject writes outside /workspace"
            )

            # ... while the per-job workspace bind mount stays writable.
            inside = await runner.exec_step(
                handle, ExecStepRequest(command="touch ./scratch && rm ./scratch")
            )
            assert_eq(inside.exit_code, 0)

            # All Linux capabilities dropped: the effective capability set is empty.
            caps = await runner.exec_step(
                handle, ExecStepRequest(command="grep CapEff /proc/self/status")
            )
            assert_eq(caps.exit_code, 0)
            assert "0000000000000000" in caps.stdout, (
                f"expected an empty effective capability set, got: {caps.stdout!r}"
            )

            # Egress denied: the pod is on an `internal` network with no route to
            # the internet, so an outbound connection cannot be established.
            egress = await runner.exec_step(
                handle,
                ExecStepRequest(command="wget -T 5 -q -O - http://1.1.1.1/"),
            )
            assert egress.exit_code != 0, "internal network must deny egress"
        finally:
            await runner.cleanup_job(handle)
            shutil.rmtree(data_dir, ignore_errors=True)
