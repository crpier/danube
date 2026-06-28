"""Webhook ingestion endpoints: `/webhooks/github` and `/webhooks/gitlab`.

Each endpoint authenticates the request against the configured provider secret,
parses the body into a normalized `WebhookEvent`, resolves the configured
pipelines it matches, and enqueues a run for each through the shared
`JobManager` trigger path — so webhook runs get the same deduplication and
concurrency cap as cron and manual triggers, recording `TriggerType.WEBHOOK` and
a ``branch/sha`` `trigger_ref`.

Failure handling follows the issue and the observability doc:

- a request that fails signature/token verification is rejected `401` and
  enqueues nothing (logged at WARNING);
- a payload that is malformed for its event type is rejected `400`;
- a valid event that matches no pipeline (or an event type Danube ignores) is a
  clean `200` no-op, logged at WARNING when a real event simply matched nothing.

These routes are mounted only when `create_app` is given both a `JobManager` and
a `WebhookConfig`.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, cast

from fastapi import APIRouter, HTTPException, Request, status
from snekql.sqlite import select

from danube.api.deps import DbDep, JobManagerDep, WebhookConfigDep
from danube.api.schemas import WebhookResponse
from danube.db.models import Pipeline
from danube.domain.enums import TriggerType
from danube.webhooks import (
    WebhookParseError,
    parse_github,
    parse_gitlab,
    pipeline_matches,
    verify_github_signature,
    verify_gitlab_token,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from danube.orchestrator import JobManager
    from danube.webhooks import WebhookEvent

router = APIRouter(prefix="/webhooks", tags=["webhooks"])
logger = logging.getLogger("danube.webhooks")

_GITHUB_SIGNATURE_HEADER = "X-Hub-Signature-256"
_GITHUB_EVENT_HEADER = "X-GitHub-Event"
_GITLAB_TOKEN_HEADER = "X-Gitlab-Token"  # noqa: S105 - HTTP header name, not a secret
_GITLAB_EVENT_HEADER = "X-Gitlab-Event"


@router.post("/github")
async def github_webhook(
    request: Request,
    db: DbDep,
    manager: JobManagerDep,
    config: WebhookConfigDep,
) -> WebhookResponse:
    """Ingest a GitHub push/pull_request webhook and enqueue matching runs."""
    body = await request.body()
    signature = request.headers.get(_GITHUB_SIGNATURE_HEADER)
    if config.github_secret is None or not verify_github_signature(
        config.github_secret, body, signature
    ):
        logger.warning("rejected GitHub webhook: signature verification failed")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid webhook signature",
        )
    event = _parse_event(
        parse_github, request.headers.get(_GITHUB_EVENT_HEADER, ""), body
    )
    return await _enqueue_matching(db, manager, event)


@router.post("/gitlab")
async def gitlab_webhook(
    request: Request,
    db: DbDep,
    manager: JobManagerDep,
    config: WebhookConfigDep,
) -> WebhookResponse:
    """Ingest a GitLab push/merge_request webhook and enqueue matching runs."""
    body = await request.body()
    token = request.headers.get(_GITLAB_TOKEN_HEADER)
    if config.gitlab_token is None or not verify_gitlab_token(
        config.gitlab_token, token
    ):
        logger.warning("rejected GitLab webhook: token verification failed")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid webhook token",
        )
    event = _parse_event(
        parse_gitlab, request.headers.get(_GITLAB_EVENT_HEADER, ""), body
    )
    return await _enqueue_matching(db, manager, event)


def _parse_event(
    parser: Callable[[str, dict[str, object]], WebhookEvent | None],
    event_type: str,
    body: bytes,
) -> WebhookEvent | None:
    """Decode ``body`` and parse it, mapping any malformed payload to a 400."""
    try:
        loaded: object = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="malformed webhook payload",
        ) from None
    if not isinstance(loaded, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="webhook payload must be a JSON object",
        )
    try:
        return parser(event_type, cast("dict[str, object]", loaded))
    except WebhookParseError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)
        ) from None


async def _enqueue_matching(
    db: DbDep, manager: JobManager, event: WebhookEvent | None
) -> WebhookResponse:
    """Enqueue a webhook run for every pipeline the event matches."""
    if event is None:
        return WebhookResponse(triggered=[])
    async with db.transaction() as tx:
        pipelines = await tx.fetch_all(select(Pipeline).all())
    matched = [pipeline for pipeline in pipelines if pipeline_matches(pipeline, event)]
    if not matched:
        logger.warning(
            "webhook for ref %s matched no configured pipeline (repos=%s)",
            event.trigger_ref,
            list(event.repo_urls),
        )
        return WebhookResponse(triggered=[])
    triggered: list[str] = []
    for pipeline in matched:
        job = await manager.trigger(pipeline.id, TriggerType.WEBHOOK, event.trigger_ref)
        triggered.append(job.id)
    return WebhookResponse(triggered=triggered)
