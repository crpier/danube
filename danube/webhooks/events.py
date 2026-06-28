"""Parse provider webhook payloads into a single normalized `WebhookEvent`.

GitHub and GitLab speak different JSON, but the Master only cares about three
things: which repository the event names, which branch it touched, and the head
commit sha. `parse_github` and `parse_gitlab` reduce a decoded payload to that
shape, returning `None` for events that should be cleanly ignored (anything
other than a branch push or an actionable pull/merge request — pings, tag pushes,
branch deletions, and PR/MR actions that do not warrant a build).

A payload whose *shape* is wrong for its declared event type (missing required
fields, wrong JSON types) raises `WebhookParseError`; the HTTP layer maps that to
a 400. Distinguishing "ignore this event" (`None`) from "this payload is
malformed" (`WebhookParseError`) is deliberate: the former is a normal no-op, the
latter is a client error worth surfacing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

# GitHub uses an all-zero sha to signal a deleted branch on a push event.
_DELETED_SHA = "0" * 40
_BRANCH_REF_PREFIX = "refs/heads/"
# PR/MR actions that should start (or re-start) a build; other actions are no-ops.
_GITHUB_PR_ACTIONS = frozenset({"opened", "synchronize", "reopened"})
_GITLAB_MR_ACTIONS = frozenset({"open", "reopen", "update"})
# Repository URL fields each provider includes; collected so a pipeline configured
# with any of the clone/ssh/web forms still matches.
_GITHUB_URL_FIELDS = ("clone_url", "ssh_url", "html_url", "git_url")
_GITLAB_URL_FIELDS = ("git_http_url", "git_ssh_url", "web_url")


class WebhookParseError(Exception):
    """Raised when a webhook payload is structurally invalid for its event type."""


@dataclass(frozen=True, slots=True)
class WebhookEvent:
    """A normalized Git webhook: the repo URLs it names, its branch, and head sha."""

    repo_urls: tuple[str, ...]
    branch: str
    sha: str

    @property
    def trigger_ref(self) -> str:
        """The `branch/sha` reference recorded on jobs this event triggers."""
        return f"{self.branch}/{self.sha}"


def parse_github(event_type: str, payload: dict[str, object]) -> WebhookEvent | None:
    """Reduce a GitHub webhook to a `WebhookEvent`, or `None` to ignore it."""
    if event_type == "push":
        branch = _branch_from_ref(_as_str(_field(payload, "ref")))
        sha = _as_str(_field(payload, "after"))
        if branch is None or sha == _DELETED_SHA:
            return None
        repo_urls = _collect_urls(
            _as_dict(_field(payload, "repository")), _GITHUB_URL_FIELDS
        )
        return WebhookEvent(repo_urls=repo_urls, branch=branch, sha=sha)
    if event_type == "pull_request":
        if _as_str(_field(payload, "action")) not in _GITHUB_PR_ACTIONS:
            return None
        head = _as_dict(_field(_as_dict(_field(payload, "pull_request")), "head"))
        repo_urls = _collect_urls(
            _as_dict(_field(payload, "repository")), _GITHUB_URL_FIELDS
        )
        return WebhookEvent(
            repo_urls=repo_urls,
            branch=_as_str(_field(head, "ref")),
            sha=_as_str(_field(head, "sha")),
        )
    return None


def parse_gitlab(event_type: str, payload: dict[str, object]) -> WebhookEvent | None:
    """Reduce a GitLab webhook to a `WebhookEvent`, or `None` to ignore it."""
    if event_type == "Push Hook":
        branch = _branch_from_ref(_as_str(_field(payload, "ref")))
        checkout_sha = _field(payload, "checkout_sha")
        if branch is None or checkout_sha is None:
            return None
        repo_urls = _collect_urls(
            _as_dict(_field(payload, "project")), _GITLAB_URL_FIELDS
        )
        return WebhookEvent(
            repo_urls=repo_urls, branch=branch, sha=_as_str(checkout_sha)
        )
    if event_type == "Merge Request Hook":
        attributes = _as_dict(_field(payload, "object_attributes"))
        if _as_str(_field(attributes, "action")) not in _GITLAB_MR_ACTIONS:
            return None
        last_commit = _as_dict(_field(attributes, "last_commit"))
        repo_urls = _collect_urls(
            _as_dict(_field(payload, "project")), _GITLAB_URL_FIELDS
        )
        return WebhookEvent(
            repo_urls=repo_urls,
            branch=_as_str(_field(attributes, "source_branch")),
            sha=_as_str(_field(last_commit, "id")),
        )
    return None


def _branch_from_ref(ref: str) -> str | None:
    """Return the branch name for a `refs/heads/*` ref, else `None` (e.g. tags)."""
    if not ref.startswith(_BRANCH_REF_PREFIX):
        return None
    return ref.removeprefix(_BRANCH_REF_PREFIX)


def _collect_urls(repo: dict[str, object], fields: tuple[str, ...]) -> tuple[str, ...]:
    """Gather the repository URL strings present under `fields`.

    Raises `WebhookParseError` if none are present: a push/PR with no resolvable
    repository URL is malformed, not merely unmatched.
    """
    urls = tuple(value for field in fields if isinstance(value := repo.get(field), str))
    if not urls:
        msg = "webhook payload names no repository URL"
        raise WebhookParseError(msg)
    return urls


def _field(payload: dict[str, object], key: str) -> object:
    if key not in payload:
        msg = f"webhook payload missing required field {key!r}"
        raise WebhookParseError(msg)
    return payload[key]


def _as_str(value: object) -> str:
    if not isinstance(value, str):
        msg = f"expected a string in webhook payload, got {type(value).__name__}"
        raise WebhookParseError(msg)
    return value


def _as_dict(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        msg = f"expected an object in webhook payload, got {type(value).__name__}"
        raise WebhookParseError(msg)
    return cast("dict[str, object]", value)
