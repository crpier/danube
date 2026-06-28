"""Integration tests for webhook ingestion (`/webhooks/github`, `/webhooks/gitlab`).

Everything runs in-process, mirroring `test_control_api`: a real
`JobOrchestrator` + `JobManager` back a FastAPI app served through
`httpx.ASGITransport`, with the webhook secrets supplied via `WebhookConfig`.
Triggered jobs run the real Coordinator entrypoint against a `FakeRunner`, so a
webhook that matches a pipeline travels the same enqueue/dispatch path a manual
trigger would.

These cover the acceptance criteria: a valid signed push enqueues exactly one job
with the right `trigger_ref`; a bad signature/token is rejected and enqueues
nothing; an event for an unmatched repo or filtered-out branch is a clean no-op;
and concurrent identical pushes dedup onto a single job.
"""

import hashlib
import hmac
import json
import shutil
import tempfile
from collections.abc import AsyncGenerator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

import anyio
import httpx
from snekql.sqlite import Database, delete, insert
from snektest import assert_eq, fixture, load_fixture, test

from danube import coordinator
from danube.api import create_app
from danube.db import open_database
from danube.db.models import Pipeline, Team
from danube.domain.enums import TriggerType
from danube.orchestrator import JobManager, JobOrchestrator
from danube.rpc import ControlPlane
from danube.runner import FakeRunner
from danube.sdk import DanubeClient
from danube.sdk.client import ENV_JOB_ID, ENV_RPC_TOKEN
from danube.security import SecretCipher, SecretService, generate_key
from danube.webhooks import WebhookConfig

RPC_ADDRESS = "http://master.test:9000"
GITHUB_SECRET = "gh-secret"
GITLAB_TOKEN = "gl-token"
REPO_URL = "https://github.com/acme/app.git"
OTHER_REPO_URL = "https://github.com/acme/other.git"


async def success_pipeline(danube: DanubeClient) -> None:
    """Report success immediately; needs no scripted Worker commands."""
    _ = await danube.status.report("success")


async def hanging_pipeline(danube: DanubeClient) -> None:
    """Block forever so a triggered job stays active for dedup assertions."""
    await anyio.sleep_forever()


@dataclass
class Harness:
    db: Database
    http: httpx.AsyncClient | None = field(default=None)
    pipeline: coordinator.Pipeline | None = field(default=None)

    @property
    def client(self) -> httpx.AsyncClient:
        assert self.http is not None
        return self.http


async def _seed_pipelines(db: Database) -> None:
    async with db.transaction() as tx:
        await tx.execute(insert(Team(id="t1", name="team", global_admin=False)))
        await tx.execute(
            insert(
                Pipeline(
                    id="p1",
                    name="any-branch",
                    team_id="t1",
                    repo_url=REPO_URL,
                    worker_image="busybox:latest",
                )
            )
        )
        await tx.execute(
            insert(
                Pipeline(
                    id="p-main",
                    name="main-only",
                    team_id="t1",
                    repo_url=REPO_URL,
                    branch_filter="main",
                    worker_image="busybox:latest",
                )
            )
        )


@asynccontextmanager
async def _make_harness() -> AsyncGenerator[Harness]:
    data_dir = Path(tempfile.mkdtemp(prefix="danube-webhook-"))
    db = await open_database(":memory:")
    harness = Harness(db=db, pipeline=success_pipeline)

    async def program(env: Mapping[str, str]) -> int:
        assert harness.http is not None
        assert harness.pipeline is not None
        client = DanubeClient(harness.http, env[ENV_JOB_ID], env[ENV_RPC_TOKEN])
        return await coordinator.run(client, harness.pipeline)

    runner = FakeRunner(coordinator=program)
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
        await _seed_pipelines(db)
        async with JobManager(orchestrator) as manager:
            app = create_app(
                db,
                control_plane=control_plane,
                job_manager=manager,
                webhook_config=WebhookConfig(
                    github_secret=GITHUB_SECRET, gitlab_token=GITLAB_TOKEN
                ),
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


def _github_sign(body: bytes) -> str:
    digest = hmac.new(GITHUB_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _github_push_body(repo_url: str, branch: str, sha: str) -> bytes:
    payload = {
        "ref": f"refs/heads/{branch}",
        "after": sha,
        "repository": {
            "clone_url": repo_url,
            "html_url": repo_url.removesuffix(".git"),
        },
    }
    return json.dumps(payload).encode()


def _github_pr_body(repo_url: str, branch: str, sha: str) -> bytes:
    payload = {
        "action": "opened",
        "pull_request": {"head": {"ref": branch, "sha": sha}},
        "repository": {
            "clone_url": repo_url,
            "html_url": repo_url.removesuffix(".git"),
        },
    }
    return json.dumps(payload).encode()


def _gitlab_push_body(repo_url: str, branch: str, sha: str) -> bytes:
    payload = {
        "ref": f"refs/heads/{branch}",
        "checkout_sha": sha,
        "project": {"git_http_url": repo_url, "web_url": repo_url.removesuffix(".git")},
    }
    return json.dumps(payload).encode()


async def _post_github(
    client: httpx.AsyncClient,
    body: bytes,
    *,
    event: str = "push",
    signature: str | None = None,
) -> httpx.Response:
    headers = {"X-GitHub-Event": event}
    headers["X-Hub-Signature-256"] = (
        signature if signature is not None else (_github_sign(body))
    )
    return await client.post("/webhooks/github", content=body, headers=headers)


async def _post_gitlab(
    client: httpx.AsyncClient, body: bytes, *, token: str = GITLAB_TOKEN
) -> httpx.Response:
    headers = {"X-Gitlab-Event": "Push Hook", "X-Gitlab-Token": token}
    return await client.post("/webhooks/gitlab", content=body, headers=headers)


async def _jobs(client: httpx.AsyncClient) -> list[dict[str, object]]:
    response = await client.get("/api/v1/jobs")
    items: list[dict[str, object]] = response.json()["items"]
    return items


@test(mark="medium")
async def test_github_valid_push_enqueues_one_job_with_ref() -> None:
    h = await load_fixture(harness())
    body = _github_push_body(REPO_URL, "main", "abc123")

    response = await _post_github(h.client, body)

    assert_eq(response.status_code, 200)
    # Both pipelines on this repo match a push to `main` (p1 any-branch, p-main).
    assert_eq(len(response.json()["triggered"]), 2)
    jobs = await _jobs(h.client)
    assert_eq(len(jobs), 2)
    for job in jobs:
        assert_eq(job["trigger_type"], TriggerType.WEBHOOK)
        assert_eq(job["trigger_ref"], "main/abc123")


@test(mark="medium")
async def test_github_pull_request_enqueues_job_with_head_ref() -> None:
    h = await load_fixture(harness())
    # Delete the `main`-filtered pipeline so only the any-branch one matches.
    async with h.db.transaction() as tx:
        _ = await tx.execute(delete(Pipeline).where(Pipeline.id.eq("p-main")))
    body = _github_pr_body(REPO_URL, "feature", "pr789")

    response = await _post_github(h.client, body, event="pull_request")

    assert_eq(response.status_code, 200)
    assert_eq(len(response.json()["triggered"]), 1)
    jobs = await _jobs(h.client)
    assert_eq(len(jobs), 1)
    assert_eq(jobs[0]["trigger_type"], TriggerType.WEBHOOK)
    assert_eq(jobs[0]["trigger_ref"], "feature/pr789")


@test(mark="medium")
async def test_github_bad_signature_is_401_and_enqueues_nothing() -> None:
    h = await load_fixture(harness())
    body = _github_push_body(REPO_URL, "main", "abc123")

    response = await _post_github(h.client, body, signature="sha256=deadbeef")

    assert_eq(response.status_code, 401)
    assert_eq(await _jobs(h.client), [])


@test(mark="medium")
async def test_github_unmatched_repo_is_clean_no_op() -> None:
    h = await load_fixture(harness())
    body = _github_push_body(OTHER_REPO_URL, "main", "abc123")

    response = await _post_github(h.client, body)

    assert_eq(response.status_code, 200)
    assert_eq(response.json()["triggered"], [])
    assert_eq(await _jobs(h.client), [])


@test(mark="medium")
async def test_github_filtered_out_branch_is_clean_no_op() -> None:
    h = await load_fixture(harness())
    # Delete the unfiltered pipeline so only the `main`-filtered one remains.
    async with h.db.transaction() as tx:
        _ = await tx.execute(delete(Pipeline).where(Pipeline.id.eq("p1")))
    body = _github_push_body(REPO_URL, "dev", "abc123")

    response = await _post_github(h.client, body)

    assert_eq(response.status_code, 200)
    assert_eq(response.json()["triggered"], [])
    assert_eq(await _jobs(h.client), [])


@test(mark="medium")
async def test_github_malformed_payload_is_400() -> None:
    h = await load_fixture(harness())
    body = b"not json"

    response = await _post_github(h.client, body)

    assert_eq(response.status_code, 400)
    assert_eq(await _jobs(h.client), [])


@test(mark="medium")
async def test_github_concurrent_pushes_dedup_onto_one_job() -> None:
    h = await load_fixture(harness())
    h.pipeline = hanging_pipeline
    # Keep one pipeline so a single job is expected for the dedup assertion.
    async with h.db.transaction() as tx:
        _ = await tx.execute(delete(Pipeline).where(Pipeline.id.eq("p-main")))
    body = _github_push_body(REPO_URL, "main", "abc123")

    first = await _post_github(h.client, body)
    second = await _post_github(h.client, body)

    assert_eq(first.status_code, 200)
    assert_eq(second.status_code, 200)
    assert_eq(first.json()["triggered"], second.json()["triggered"])
    assert_eq(len(await _jobs(h.client)), 1)


@test(mark="medium")
async def test_gitlab_valid_push_enqueues_job() -> None:
    h = await load_fixture(harness())
    async with h.db.transaction() as tx:
        _ = await tx.execute(delete(Pipeline).where(Pipeline.id.eq("p-main")))
    body = _gitlab_push_body(REPO_URL, "main", "abc123")

    response = await _post_gitlab(h.client, body)

    assert_eq(response.status_code, 200)
    assert_eq(len(response.json()["triggered"]), 1)
    jobs = await _jobs(h.client)
    assert_eq(len(jobs), 1)
    assert_eq(jobs[0]["trigger_type"], TriggerType.WEBHOOK)
    assert_eq(jobs[0]["trigger_ref"], "main/abc123")


@test(mark="medium")
async def test_gitlab_bad_token_is_401_and_enqueues_nothing() -> None:
    h = await load_fixture(harness())
    body = _gitlab_push_body(REPO_URL, "main", "abc123")

    response = await _post_gitlab(h.client, body, token="wrong")

    assert_eq(response.status_code, 401)
    assert_eq(await _jobs(h.client), [])
