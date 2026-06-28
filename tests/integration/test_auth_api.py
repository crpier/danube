"""Integration tests for UI/API authentication and team-based RBAC.

Runs the full app in-process (real orchestrator + control plane over an
`httpx.ASGITransport`, the same loop as `test_control_api`) but with an
`AuthConfig` wired in. Seeds users/teams/permissions, mints HS256 tokens with the
embedded-IdP helper, and asserts the acceptance criteria from issue #27:

- unauthenticated request -> 401
- authenticated user without team permission -> 403
- authorized user can view / trigger / cancel
- expired or invalid token -> 401
- health and metrics stay reachable without a token
"""

import shutil
import tempfile
import time
from collections.abc import AsyncGenerator, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

import anyio
import httpx
from snekql.sqlite import Database, insert
from snektest import assert_eq, fixture, load_fixture, test

from danube import coordinator
from danube.api import create_app
from danube.auth import AuthConfig, encode
from danube.db import open_database
from danube.db.models import (
    Pipeline,
    PipelinePermission,
    Team,
    TeamMember,
    User,
)
from danube.domain.enums import JobStatus
from danube.domain.lifecycle import TERMINAL_STATES
from danube.domain.runner_types import ExecResult
from danube.orchestrator import JobManager, JobOrchestrator
from danube.rpc import ControlPlane
from danube.runner import FakeRunner
from danube.sdk import DanubeClient
from danube.sdk.client import ENV_JOB_ID, ENV_RPC_TOKEN
from danube.security import SecretCipher, SecretService, generate_key

RPC_ADDRESS = "http://master.test:9000"
SECRET = "integration-signing-secret"
ISSUER = "https://idp.test"
AUDIENCE = "danube"

AUTH_CONFIG = AuthConfig(
    issuer=ISSUER, audience=AUDIENCE, algorithm="HS256", secret=SECRET
)


def _token(subject: str, *, email: str | None = None, exp_offset: int = 3600) -> str:
    payload: dict[str, object] = {
        "sub": subject,
        "iss": ISSUER,
        "aud": AUDIENCE,
        "exp": int(time.time()) + exp_offset,
    }
    if email is not None:
        payload["email"] = email
    return encode(payload, secret=SECRET)


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def two_step_pipeline(danube: DanubeClient) -> None:
    _ = await danube.step.run("echo one", name="one")
    _ = await danube.step.run("echo two", name="two")
    _ = await danube.status.report("success")


async def hanging_pipeline(danube: DanubeClient) -> None:
    _ = await danube.step.run("echo one", name="one")
    await anyio.sleep_forever()


@dataclass
class Harness:
    db: Database
    runner: FakeRunner
    http: httpx.AsyncClient | None = field(default=None)
    pipeline: coordinator.Pipeline | None = field(default=None)

    @property
    def client(self) -> httpx.AsyncClient:
        assert self.http is not None
        return self.http


async def _seed(db: Database) -> None:
    """Seed two teams: `engineering` (write on the pipeline) and `viewers` (none).

    `dev` is a member of engineering; `guest` is a member of viewers and thus an
    authenticated user with no permission on the pipeline.
    """
    async with db.transaction() as tx:
        await tx.execute(
            insert(
                User(
                    id="u-dev",
                    email="dev@example.com",
                    name="dev",
                    oidc_subject="sub-dev",
                )
            )
        )
        await tx.execute(
            insert(
                User(
                    id="u-guest",
                    email="guest@example.com",
                    name="guest",
                    oidc_subject="sub-guest",
                )
            )
        )
        await tx.execute(insert(Team(id="t-eng", name="engineering")))
        await tx.execute(insert(Team(id="t-view", name="viewers")))
        await tx.execute(insert(TeamMember(team_id="t-eng", user_id="u-dev")))
        await tx.execute(insert(TeamMember(team_id="t-view", user_id="u-guest")))
        await tx.execute(
            insert(
                Pipeline(
                    id="p1",
                    name="demo",
                    team_id="t-eng",
                    repo_url="https://example.test/repo.git",
                    worker_image="busybox:latest",
                )
            )
        )
        await tx.execute(
            insert(PipelinePermission(pipeline_id="p1", team_id="t-eng", level="write"))
        )


@asynccontextmanager
async def _make_harness() -> AsyncGenerator[Harness]:
    data_dir = Path(tempfile.mkdtemp(prefix="danube-auth-"))
    db = await open_database(":memory:")
    harness = Harness(db=db, runner=FakeRunner())

    async def program(env: Mapping[str, str]) -> int:
        assert harness.http is not None
        assert harness.pipeline is not None
        client = DanubeClient(harness.http, env[ENV_JOB_ID], env[ENV_RPC_TOKEN])
        return await coordinator.run(client, harness.pipeline)

    runner = FakeRunner(coordinator=program)
    harness.runner = runner
    control_plane = ControlPlane(
        runner,
        db,
        data_dir,
        secret_service=SecretService(db, SecretCipher(generate_key())),
    )
    orchestrator = JobOrchestrator(
        runner, db, data_dir, control_plane, rpc_address=RPC_ADDRESS
    )
    try:
        await _seed(db)
        async with JobManager(orchestrator) as manager:
            app = create_app(
                db,
                control_plane=control_plane,
                job_manager=manager,
                auth=AUTH_CONFIG,
            )
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://master"
            ) as http:
                harness.http = http
                yield harness
    finally:
        await db.close()
        shutil.rmtree(data_dir, ignore_errors=True)


@fixture
async def harness() -> AsyncGenerator[Harness]:
    async with _make_harness() as resources:
        yield resources


async def _wait_for(
    http: httpx.AsyncClient,
    job_id: str,
    predicate: Callable[[str], bool],
    headers: dict[str, str],
    timeout_seconds: float = 5.0,
) -> dict[str, object]:
    with anyio.fail_after(timeout_seconds):
        while True:
            response = await http.get(f"/api/v1/jobs/{job_id}", headers=headers)
            body = response.json()
            if predicate(body["status"]):
                return body
            await anyio.sleep(0.01)


def _is_terminal(status: str) -> bool:
    return JobStatus(status) in TERMINAL_STATES


@test(mark="medium")
async def test_unauthenticated_request_is_401() -> None:
    h = await load_fixture(harness())

    run = await h.client.post("/api/v1/pipelines/p1/run")
    view = await h.client.get("/api/v1/pipelines/p1")
    listing = await h.client.get("/api/v1/pipelines")

    assert_eq(run.status_code, 401)
    assert_eq(view.status_code, 401)
    assert_eq(listing.status_code, 401)


@test(mark="medium")
async def test_invalid_token_is_401() -> None:
    h = await load_fixture(harness())

    response = await h.client.get(
        "/api/v1/pipelines/p1", headers=_auth("garbage.token.here")
    )

    assert_eq(response.status_code, 401)


@test(mark="medium")
async def test_expired_token_is_401() -> None:
    h = await load_fixture(harness())
    token = _token("sub-dev", exp_offset=-10)

    response = await h.client.get("/api/v1/pipelines/p1", headers=_auth(token))

    assert_eq(response.status_code, 401)


@test(mark="medium")
async def test_authenticated_without_permission_is_403() -> None:
    h = await load_fixture(harness())
    token = _token("sub-guest")

    run = await h.client.post("/api/v1/pipelines/p1/run", headers=_auth(token))
    view = await h.client.get("/api/v1/pipelines/p1", headers=_auth(token))

    assert_eq(run.status_code, 403)
    assert_eq(view.status_code, 403)


@test(mark="medium")
async def test_authorized_user_can_view() -> None:
    h = await load_fixture(harness())
    token = _token("sub-dev")

    view = await h.client.get("/api/v1/pipelines/p1", headers=_auth(token))
    listing = await h.client.get("/api/v1/pipelines", headers=_auth(token))

    assert_eq(view.status_code, 200)
    assert_eq(view.json()["id"], "p1")
    assert_eq(listing.status_code, 200)
    assert_eq([item["id"] for item in listing.json()["items"]], ["p1"])


@test(mark="medium")
async def test_list_filtered_for_user_without_permission() -> None:
    h = await load_fixture(harness())
    token = _token("sub-guest")

    listing = await h.client.get("/api/v1/pipelines", headers=_auth(token))

    assert_eq(listing.status_code, 200)
    assert_eq(listing.json()["items"], [])


@test(mark="medium")
async def test_authorized_user_can_trigger_and_cancel() -> None:
    h = await load_fixture(harness())
    h.pipeline = hanging_pipeline
    h.runner.script_command(
        "echo one", ExecResult(exit_code=0, stdout="one\n", stderr="")
    )
    token = _token("sub-dev")

    triggered = await h.client.post("/api/v1/pipelines/p1/run", headers=_auth(token))
    assert_eq(triggered.status_code, 202)
    job_id = triggered.json()["id"]

    _ = await _wait_for(
        h.client, job_id, lambda s: s == JobStatus.RUNNING, _auth(token)
    )
    cancelled = await h.client.post(
        f"/api/v1/jobs/{job_id}/cancel", headers=_auth(token)
    )
    assert_eq(cancelled.status_code, 202)
    final = await _wait_for(h.client, job_id, _is_terminal, _auth(token))
    assert_eq(final["status"], JobStatus.CANCELLED)


@test(mark="medium")
async def test_guest_cannot_cancel_others_job() -> None:
    h = await load_fixture(harness())
    h.pipeline = two_step_pipeline
    h.runner.script_command(
        "echo one", ExecResult(exit_code=0, stdout="one\n", stderr="")
    )
    h.runner.script_command(
        "echo two", ExecResult(exit_code=0, stdout="two\n", stderr="")
    )
    dev = _token("sub-dev")
    triggered = await h.client.post("/api/v1/pipelines/p1/run", headers=_auth(dev))
    job_id = triggered.json()["id"]

    guest = _token("sub-guest")
    response = await h.client.post(
        f"/api/v1/jobs/{job_id}/cancel", headers=_auth(guest)
    )

    assert_eq(response.status_code, 403)


@test(mark="medium")
async def test_health_and_metrics_reachable_without_token() -> None:
    h = await load_fixture(harness())

    health = await h.client.get("/health")
    ready = await h.client.get("/health/ready")
    metrics = await h.client.get("/metrics")

    assert_eq(health.status_code, 200)
    assert_eq(ready.status_code, 200)
    assert_eq(metrics.status_code, 200)
