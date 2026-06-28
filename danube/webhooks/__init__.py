"""Git webhook ingestion: authenticate, parse, and match provider events.

This package holds the provider-agnostic core behind `/webhooks/github` and
`/webhooks/gitlab`: `WebhookConfig` carries the per-provider secrets, `verify`
authenticates a request, `events` reduces a payload to a normalized
`WebhookEvent`, and `matching` resolves which pipelines an event triggers. The
HTTP wiring lives in `danube.api.routes.webhooks`; everything here is pure and
unit-testable without FastAPI.
"""

from danube.webhooks.config import WebhookConfig
from danube.webhooks.events import (
    WebhookEvent,
    WebhookParseError,
    parse_github,
    parse_gitlab,
)
from danube.webhooks.matching import normalize_repo_url, pipeline_matches
from danube.webhooks.verify import verify_github_signature, verify_gitlab_token

__all__ = [
    "WebhookConfig",
    "WebhookEvent",
    "WebhookParseError",
    "normalize_repo_url",
    "parse_github",
    "parse_gitlab",
    "pipeline_matches",
    "verify_github_signature",
    "verify_gitlab_token",
]
