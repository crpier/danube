"""End-to-end job execution against real rootless Podman (issue #18).

This is the production-shaped variant of `test_job_run.py`: a real Coordinator
container boots the SDK (`python -m danube.coordinator`), imports a `danubefile.py`
from the shared workspace, and drives Worker steps over the Master RPC, exactly as
`docs/architecture/execution-model.md` describes. It registers only when a Podman
socket is present, so it skips on hosts without rootless Podman (the validation
gate accepts that).

Two things this test has to solve that the in-process variant does not:

1. **Coordinator dependencies without an image build.** Per the chosen approach we
   use a stock ``python`` image and bind-mount this repo plus the project's
   virtualenv ``site-packages`` (read-only) onto ``PYTHONPATH`` so
   ``python -m danube.coordinator`` and its deps (httpx, anyio, pydantic) import.

2. **RPC reachability from the pod.** The default job network is ``internal`` (no
   host route), which is correct for the Worker's deny-by-default egress but blocks
   the Coordinator from reaching the Master. For this test we pre-create a
   non-``internal`` control network and point the pod at it, then address the
   Master at ``host.containers.internal``. Productizing this split (control network
   reachable, egress still denied) is tracked separately; egress-proxy work is out
   of scope for #18.

NOTE: this environment has no Podman socket, so this test is UNVERIFIED here. It is
written to the spec above and may need adjustment on a real rootless-Podman host
(notably the ``host.containers.internal`` route and the site-packages ABI match).
"""

import shutil
import socket
import tempfile
from collections.abc import AsyncGenerator
from pathlib import Path

import anyio
import httpx
import uvicorn
from snekql.sqlite import Database, Fetched, insert, select
from snektest import assert_eq, fixture, load_fixture, test

import danube
from danube.api import create_app
from danube.db import open_database
from danube.db.models import Pipeline, Step, Team
from danube.domain.enums import JobStatus, StepStatus, TriggerType
from danube.orchestrator import JobOrchestrator
from danube.rpc import ControlPlane
from danube.runner.local import LocalContainerRunner, LocalRunnerConfig
from danube.runner.podman import (
    Mount,
    PodmanAdapter,
    build_async_client,
    socket_available,
)

WORKER_IMAGE = "docker.io/library/busybox:latest"
COORDINATOR_IMAGE = "docker.io/library/python:3.14-slim"
CONTROL_NETWORK = "danube-e2e-control"

REPO_ROOT = Path(danube.__file__).resolve().parent.parent
SITE_PACKAGES = Path(httpx.__file__).resolve().parent.parent

DANUBEFILE = """
async def pipeline(danube):
    await danube.step.run("echo one", name="one")
    await danube.step.run("echo two", name="two")
    await danube.status.report("success")
"""


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        # Bind all interfaces so the address is reachable from the job pod, not
        # just loopback (the Coordinator connects over the control network).
        sock.bind(("0.0.0.0", 0))  # noqa: S104
        return sock.getsockname()[1]


async def _seed(db: Database) -> str:
    async with db.transaction() as tx:
        await tx.execute(insert(Team(id="t1", name="team", global_admin=False)))
        await tx.execute(
            insert(
                Pipeline(
                    id="p1",
                    name="demo",
                    team_id="t1",
                    repo_url="https://example.test/repo.git",
                    worker_image=WORKER_IMAGE,
                    max_duration_seconds=120,
                )
            )
        )
    return "p1"


if socket_available():

    @fixture
    async def podman_client() -> AsyncGenerator[httpx.AsyncClient]:
        client = build_async_client()
        try:
            yield client
        finally:
            await client.aclose()

    @test(mark="slow")
    async def test_job_runs_to_success_over_podman() -> None:
        client = await load_fixture(podman_client())
        adapter = PodmanAdapter(client)
        # A non-internal control network so the Coordinator can reach the Master;
        # the runner's own `ensure_network` is idempotent and leaves this as-is.
        await adapter.ensure_network(
            CONTROL_NETWORK, internal=False, labels={"io.danube.managed": "true"}
        )

        data_dir = Path(tempfile.mkdtemp(prefix="danube-e2e-"))
        db = await open_database(":memory:")
        port = _free_port()
        rpc_address = f"http://host.containers.internal:{port}"
        config = LocalRunnerConfig(
            coordinator_image=COORDINATOR_IMAGE,
            egress_network=CONTROL_NETWORK,
            coordinator_env={"PYTHONPATH": "/opt/site-packages:/opt/repo"},
            coordinator_mounts=(
                Mount(
                    source=str(SITE_PACKAGES),
                    destination="/opt/site-packages",
                    read_only=True,
                ),
                Mount(source=str(REPO_ROOT), destination="/opt/repo", read_only=True),
            ),
        )
        runner = LocalContainerRunner(adapter, data_dir, db=db, config=config)
        control_plane = ControlPlane(runner, db, data_dir)
        app = create_app(db, control_plane=control_plane)
        orchestrator = JobOrchestrator(
            runner, db, data_dir, control_plane, rpc_address=rpc_address
        )
        server = uvicorn.Server(
            # Reachable from the job pod over the control network, not just
            # loopback.
            uvicorn.Config(app, host="0.0.0.0", port=port, log_level="warning")  # noqa: S104
        )

        try:
            pipeline_id = await _seed(db)
            async with anyio.create_task_group() as tg:
                tg.start_soon(server.serve)
                await _wait_until_serving(server)

                created = await orchestrator.create_job(pipeline_id, TriggerType.MANUAL)
                # Deliver the danubefile into the job workspace the runner mounts
                # at /workspace in both containers.
                workspace = data_dir / "workspaces" / created.id
                await anyio.Path(workspace).mkdir(parents=True, exist_ok=True)
                _ = await anyio.Path(workspace / "danubefile.py").write_text(DANUBEFILE)

                job = await orchestrator.run_job(created.id)

                assert_eq(job.status, JobStatus.SUCCESS)
                steps = await _steps(db, created.id)
                assert_eq([s.name for s in steps], ["one", "two"])
                assert_eq(
                    [s.status for s in steps],
                    [StepStatus.SUCCESS, StepStatus.SUCCESS],
                )
                logged = await anyio.Path(
                    data_dir / "logs" / f"{created.id}.log"
                ).read_text()
                assert "one" in logged
                assert "two" in logged

                server.should_exit = True
        finally:
            await db.close()
            shutil.rmtree(data_dir, ignore_errors=True)


async def _wait_until_serving(server: uvicorn.Server) -> None:
    # uvicorn exposes readiness as a bool flag, not an event, so poll it.
    with anyio.fail_after(10):
        while not server.started:  # noqa: ASYNC110
            await anyio.sleep(0.05)


async def _steps(db: Database, job_id: str) -> list[Step[Fetched]]:
    async with db.transaction() as tx:
        rows = await tx.fetch_all(select(Step).where(Step.job_id.eq(job_id)))
    return sorted(rows, key=lambda step: step.sequence)
