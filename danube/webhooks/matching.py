"""Resolve which configured pipelines a webhook event should trigger.

A pipeline matches an event when its `repo_url` names the same repository as the
event and the event's branch passes the pipeline's `branch_filter`. Repository
URLs are compared after `normalize_repo_url` collapses the cosmetic differences
between clone forms — scheme, ``user@`` prefix, scp-style ``host:path``, a
trailing ``.git``, trailing slashes, and case — so a pipeline configured with the
HTTPS URL still matches an event that only carries the SSH form, and vice versa.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from snekql.sqlite import Fetched

    from danube.db.models import Pipeline
    from danube.webhooks.events import WebhookEvent


def normalize_repo_url(url: str) -> str:
    """Reduce a Git URL to a ``host/path`` form for equality comparison."""
    value = url.strip()
    if "://" in value:
        value = value.split("://", 1)[1]
    if "@" in value:
        value = value.rsplit("@", 1)[1]
    value = value.replace(":", "/")
    while "//" in value:
        value = value.replace("//", "/")
    value = value.removesuffix("/").removesuffix(".git").removesuffix("/")
    return value.lower()


def pipeline_matches(pipeline: Pipeline[Fetched], event: WebhookEvent) -> bool:
    """Return whether ``event`` should trigger ``pipeline``.

    The repository must match one of the event's URLs, and — when the pipeline
    sets a `branch_filter` — the event's branch must equal it. A pipeline without
    a `branch_filter` matches a push to any branch.
    """
    repo = normalize_repo_url(pipeline.repo_url)
    if repo not in {normalize_repo_url(candidate) for candidate in event.repo_urls}:
        return False
    return pipeline.branch_filter is None or pipeline.branch_filter == event.branch
