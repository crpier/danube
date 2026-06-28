"""Integration tests for the Coordinator <-> Master RPC control plane.

Everything runs in-process: an in-memory snekql database, a `FakeRunner`, the
`ControlPlane`, and the FastAPI app served through `httpx.ASGITransport`. The
Coordinator SDK (`DanubeClient`) is pointed at that ASGI transport, so each test
drives the real HTTP/JSON path end-to-end without Podman or a socket.
"""

import shutil
import tempfile
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path

import anyio
import httpx
from snekql.sqlite import Database, Fetched, insert, select
from snektest import assert_eq, assert_raises, fixture, load_fixture, test

from danube.api import create_app
from danube.db import open_database
from danube.db.models import Artifact, Job, Pipeline, Secret, Step, Team
from danube.domain.enums import JobStatus, StepStatus, TriggerType
from danube.domain.runner_types import ExecResult, StartJobRequest
from danube.rpc import ControlPlane
from danube.runner import FakeRunner
from danube.sdk import DanubeClient, RpcError, StepError
from danube.security import SecretCipher, SecretService, generate_key
from examples.danubefile import pipeline

JOB_ID = "j1"
TOKEN = "job-token-abc"
# One cipher shared by the seed (encrypt) and the SecretService (decrypt) so the
# stored blobs are real AES-256-GCM ciphertext rather than plaintext.
_CIPHER = SecretCipher(generate_key())


async def _seed(db: Database) -> None:
    """Seed a team, two pipelines, three secrets, and one running job on p1."""
    async with db.transaction() as tx:
        await tx.execute(insert(Team(id="t1", name="team", global_admin=False)))
        for pid, image in (("p1", "img:p1"), ("p2", "img:p2")):
            await tx.execute(
                insert(
                    Pipeline(
                        id=pid,
                        name=pid,
                        team_id="t1",
                        repo_url=f"https://example.test/{pid}.git",
                        worker_image=image,
                    )
                )
            )
        await tx.execute(
            insert(
                Secret(
                    id="s1",
                    pipeline_id="p1",
                    key="DEPLOY_TOKEN",
                    value_encrypted=_CIPHER.encrypt("tok-123"),
                )
            )
        )
        # Belongs to p2: must be denied to a p1 job.
        await tx.execute(
            insert(
                Secret(
                    id="s2",
                    pipeline_id="p2",
                    key="OTHER_SECRET",
                    value_encrypted=_CIPHER.encrypt("nope"),
                )
            )
        )
        # Global secret (no pipeline): authorized for every job.
        await tx.execute(
            insert(
                Secret(
                    id="s3",
                    pipeline_id=None,
                    key="GLOBAL",
                    value_encrypted=_CIPHER.encrypt("glob"),
                )
            )
        )
        await tx.execute(
            insert(
                Job(
                    id=JOB_ID,
                    pipeline_id="p1",
                    trigger_type=TriggerType.MANUAL,
                    status=JobStatus.RUNNING,
                )
            )
        )


@dataclass
class Harness:
    db: Database
    runner: FakeRunner
    control_plane: ControlPlane
    data_dir: Path
    http: httpx.AsyncClient

    def danube(self, job_id: str = JOB_ID, token: str = TOKEN) -> DanubeClient:
        return DanubeClient(self.http, job_id, token)

    def log_path(self, job_id: str = JOB_ID) -> Path:
        return self.data_dir / "logs" / f"{job_id}.log"


@asynccontextmanager
async def _make_harness(runner: FakeRunner) -> AsyncGenerator[Harness]:
    data_dir = Path(tempfile.mkdtemp(prefix="danube-rpc-"))
    db = await open_database(":memory:")
    control_plane = ControlPlane(
        runner, db, data_dir, secret_service=SecretService(db, _CIPHER)
    )
    try:
        await _seed(db)
        handle = await runner.start_job(
            StartJobRequest(job_id=JOB_ID, pipeline_id="p1", worker_image="img:p1")
        )
        _ = await control_plane.open_session(
            job_id=JOB_ID, pipeline_id="p1", handle=handle, token=TOKEN
        )
        app = create_app(db, control_plane=control_plane)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as http:
            yield Harness(db, runner, control_plane, data_dir, http)
    finally:
        await control_plane.close_session(JOB_ID)
        await db.close()
        shutil.rmtree(data_dir, ignore_errors=True)


@fixture
async def harness() -> AsyncGenerator[Harness]:
    async with _make_harness(FakeRunner()) as resources:
        yield resources


async def _steps(db: Database, job_id: str = JOB_ID) -> list[Step[Fetched]]:
    async with db.transaction() as tx:
        rows = await tx.fetch_all(select(Step).where(Step.job_id.eq(job_id)))
    return sorted(rows, key=lambda step: step.sequence)


@test(mark="medium")
async def test_run_step_round_trips_exit_code() -> None:
    h = await load_fixture(harness())
    h.runner.script_command("make build", ExecResult(exit_code=0, stdout="", stderr=""))

    code = await h.danube().step.run("make build", name="build")

    assert_eq(code, 0)
    steps = await _steps(h.db)
    assert_eq([s.name for s in steps], ["build"])
    assert_eq(steps[0].status, StepStatus.SUCCESS)
    assert_eq(steps[0].exit_code, 0)


@test(mark="medium")
async def test_check_true_nonzero_raises() -> None:
    h = await load_fixture(harness())
    h.runner.script_command("flaky", ExecResult(exit_code=2, stdout="", stderr="boom"))

    with assert_raises(StepError):
        await h.danube().step.run("flaky")

    # The failed step is still recorded.
    steps = await _steps(h.db)
    assert_eq(steps[0].status, StepStatus.FAILURE)
    assert_eq(steps[0].exit_code, 2)


@test(mark="medium")
async def test_check_false_returns_code() -> None:
    h = await load_fixture(harness())
    h.runner.script_command("flaky", ExecResult(exit_code=3, stdout="", stderr=""))

    code = await h.danube().step.run("flaky", check=False)

    assert_eq(code, 3)


@test(mark="medium")
async def test_capture_output_returns_streams() -> None:
    h = await load_fixture(harness())
    h.runner.script_command(
        "report", ExecResult(exit_code=0, stdout="out", stderr="err")
    )

    result = await h.danube().step.capture("report")

    assert_eq(result.exit_code, 0)
    assert_eq(result.stdout, "out")
    assert_eq(result.stderr, "err")


@test(mark="medium")
async def test_get_secret_returns_pipeline_scoped_value() -> None:
    h = await load_fixture(harness())

    assert_eq(await h.danube().secrets.get("DEPLOY_TOKEN"), "tok-123")


@test(mark="medium")
async def test_get_secret_returns_global_value() -> None:
    h = await load_fixture(harness())

    # Global (pipeline-less) secrets are authorized for every job.
    assert_eq(await h.danube().secrets.get("GLOBAL"), "glob")


@test(mark="medium")
async def test_get_secret_denied_for_unauthorized_pipeline() -> None:
    h = await load_fixture(harness())

    with assert_raises(RpcError) as caught:
        await h.danube().secrets.get("OTHER_SECRET")

    assert_eq(caught.exception.status_code, 403)


@test(mark="medium")
async def test_secret_value_is_scrubbed_from_log() -> None:
    h = await load_fixture(harness())
    # The Worker echoes the secret; it must not be written verbatim to the log.
    h.runner.script_command(
        "leak", ExecResult(exit_code=0, stdout="token is tok-123\n", stderr="")
    )

    _ = await h.danube().step.run("leak")

    logged = await anyio.Path(h.log_path()).read_text()
    assert "tok-123" not in logged
    assert "***" in logged


@test(mark="medium")
async def test_rpc_for_unknown_job_is_rejected() -> None:
    h = await load_fixture(harness())

    with assert_raises(RpcError) as caught:
        await h.danube(job_id="nope").step.run("anything")

    assert_eq(caught.exception.status_code, 404)


@test(mark="medium")
async def test_rpc_for_closed_session_is_rejected() -> None:
    h = await load_fixture(harness())
    await h.control_plane.close_session(JOB_ID)

    with assert_raises(RpcError) as caught:
        await h.danube().step.run("anything")

    assert_eq(caught.exception.status_code, 404)


@test(mark="medium")
async def test_rpc_for_inactive_job_is_rejected() -> None:
    h = await load_fixture(harness())
    # The session is still open, but the job has reached a terminal state.
    _ = await h.danube().status.report("success")

    with assert_raises(RpcError) as caught:
        await h.danube().step.run("anything")

    assert_eq(caught.exception.status_code, 404)


@test(mark="medium")
async def test_secret_in_command_is_scrubbed_from_record() -> None:
    h = await load_fixture(harness())
    leaky = "deploy --token tok-123"
    h.runner.script_command(leaky, ExecResult(exit_code=0, stdout="", stderr=""))

    _ = await h.danube().step.run(leaky)

    steps = await _steps(h.db)
    assert "tok-123" not in steps[0].command
    assert "***" in steps[0].command


@test(mark="medium")
async def test_rpc_with_invalid_token_is_rejected() -> None:
    h = await load_fixture(harness())

    with assert_raises(RpcError) as caught:
        await h.danube(token="wrong").step.run("anything")

    assert_eq(caught.exception.status_code, 401)


@test(mark="medium")
async def test_upload_artifact_records_row() -> None:
    h = await load_fixture(harness())

    uploaded = await h.danube().artifacts.upload("dist/app.tar.gz", name="app-bundle")

    assert_eq(uploaded.name, "app-bundle")
    async with h.db.transaction() as tx:
        row = await tx.fetch_one(
            select(Artifact).where(Artifact.id.eq(uploaded.artifact_id))
        )
    assert_eq(row.name, "app-bundle")
    assert_eq(row.job_id, JOB_ID)


@test(mark="medium")
async def test_report_status_transitions_job() -> None:
    h = await load_fixture(harness())

    status = await h.danube().status.report("success")

    assert_eq(status, JobStatus.SUCCESS)
    async with h.db.transaction() as tx:
        job = await tx.fetch_one(select(Job).where(Job.id.eq(JOB_ID)))
    assert_eq(job.status, JobStatus.SUCCESS)
    assert job.finished_at is not None


@test(mark="medium")
async def test_report_illegal_status_is_conflict() -> None:
    h = await load_fixture(harness())

    # running -> pending is not a legal lifecycle transition.
    with assert_raises(RpcError) as caught:
        await h.danube().status.report("pending")

    assert_eq(caught.exception.status_code, 409)


@test(mark="medium")
async def test_sample_danubefile_runs_end_to_end() -> None:
    h = await load_fixture(harness())
    h.runner.script_command(
        "echo building && make build",
        ExecResult(exit_code=0, stdout="built\n", stderr=""),
    )
    h.runner.script_command(
        "deploy --token $DEPLOY_TOKEN",
        ExecResult(exit_code=0, stdout="deployed\n", stderr=""),
    )

    await pipeline(h.danube())

    steps = await _steps(h.db)
    assert_eq([s.name for s in steps], ["build", "deploy"])
    assert_eq([s.status for s in steps], [StepStatus.SUCCESS, StepStatus.SUCCESS])
    async with h.db.transaction() as tx:
        job = await tx.fetch_one(select(Job).where(Job.id.eq(JOB_ID)))
        artifacts = await tx.fetch_all(
            select(Artifact).where(Artifact.job_id.eq(JOB_ID))
        )
    assert_eq(job.status, JobStatus.SUCCESS)
    assert_eq([a.name for a in artifacts], ["app-bundle"])
