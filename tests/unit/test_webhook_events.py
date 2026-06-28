"""Tests for parsing GitHub/GitLab webhook payloads into a `WebhookEvent`.

These cover the normalization contract: branch pushes and actionable pull/merge
requests become events carrying the repo URLs, branch, and head sha; tag pushes,
branch deletions, and non-actionable PR/MR actions are cleanly ignored (``None``);
and payloads malformed for their event type raise `WebhookParseError`.
"""

from snektest import assert_eq, assert_raises, test

from danube.webhooks import WebhookParseError, parse_github, parse_gitlab

_GITHUB_REPO = {
    "clone_url": "https://github.com/acme/app.git",
    "ssh_url": "git@github.com:acme/app.git",
    "html_url": "https://github.com/acme/app",
}
_GITLAB_PROJECT = {
    "git_http_url": "https://gitlab.com/acme/app.git",
    "git_ssh_url": "git@gitlab.com:acme/app.git",
    "web_url": "https://gitlab.com/acme/app",
}


@test(mark="fast")
def test_github_push_yields_branch_and_sha() -> None:
    event = parse_github(
        "push",
        {"ref": "refs/heads/main", "after": "abc123", "repository": _GITHUB_REPO},
    )

    assert event is not None
    assert_eq(event.branch, "main")
    assert_eq(event.sha, "abc123")
    assert_eq(event.trigger_ref, "main/abc123")
    assert "https://github.com/acme/app.git" in event.repo_urls


@test(mark="fast")
def test_github_pull_request_uses_head_ref() -> None:
    event = parse_github(
        "pull_request",
        {
            "action": "opened",
            "pull_request": {"head": {"ref": "feature", "sha": "def456"}},
            "repository": _GITHUB_REPO,
        },
    )

    assert event is not None
    assert_eq(event.branch, "feature")
    assert_eq(event.sha, "def456")


@test(mark="fast")
def test_github_tag_push_is_ignored() -> None:
    event = parse_github(
        "push",
        {"ref": "refs/tags/v1", "after": "abc123", "repository": _GITHUB_REPO},
    )

    assert_eq(event, None)


@test(mark="fast")
def test_github_branch_deletion_is_ignored() -> None:
    event = parse_github(
        "push",
        {"ref": "refs/heads/main", "after": "0" * 40, "repository": _GITHUB_REPO},
    )

    assert_eq(event, None)


@test(mark="fast")
def test_github_closed_pull_request_is_ignored() -> None:
    event = parse_github(
        "pull_request",
        {
            "action": "closed",
            "pull_request": {"head": {"ref": "feature", "sha": "def456"}},
            "repository": _GITHUB_REPO,
        },
    )

    assert_eq(event, None)


@test(mark="fast")
def test_github_unknown_event_type_is_ignored() -> None:
    assert_eq(parse_github("ping", {"zen": "hi"}), None)


@test(mark="fast")
def test_github_push_missing_repository_raises() -> None:
    with assert_raises(WebhookParseError):
        _ = parse_github("push", {"ref": "refs/heads/main", "after": "abc123"})


@test(mark="fast")
def test_github_push_without_repo_urls_raises() -> None:
    with assert_raises(WebhookParseError):
        _ = parse_github(
            "push",
            {"ref": "refs/heads/main", "after": "abc123", "repository": {}},
        )


@test(mark="fast")
def test_gitlab_push_yields_branch_and_sha() -> None:
    event = parse_gitlab(
        "Push Hook",
        {
            "ref": "refs/heads/main",
            "checkout_sha": "abc123",
            "project": _GITLAB_PROJECT,
        },
    )

    assert event is not None
    assert_eq(event.branch, "main")
    assert_eq(event.sha, "abc123")
    assert "git@gitlab.com:acme/app.git" in event.repo_urls


@test(mark="fast")
def test_gitlab_merge_request_uses_source_branch() -> None:
    event = parse_gitlab(
        "Merge Request Hook",
        {
            "object_attributes": {
                "action": "open",
                "source_branch": "feature",
                "last_commit": {"id": "def456"},
            },
            "project": _GITLAB_PROJECT,
        },
    )

    assert event is not None
    assert_eq(event.branch, "feature")
    assert_eq(event.sha, "def456")


@test(mark="fast")
def test_gitlab_branch_deletion_is_ignored() -> None:
    event = parse_gitlab(
        "Push Hook",
        {"ref": "refs/heads/main", "checkout_sha": None, "project": _GITLAB_PROJECT},
    )

    assert_eq(event, None)


@test(mark="fast")
def test_gitlab_merge_close_is_ignored() -> None:
    event = parse_gitlab(
        "Merge Request Hook",
        {
            "object_attributes": {
                "action": "close",
                "source_branch": "feature",
                "last_commit": {"id": "def456"},
            },
            "project": _GITLAB_PROJECT,
        },
    )

    assert_eq(event, None)


@test(mark="fast")
def test_gitlab_unknown_event_type_is_ignored() -> None:
    assert_eq(parse_gitlab("Pipeline Hook", {"foo": "bar"}), None)


@test(mark="fast")
def test_gitlab_push_malformed_ref_raises() -> None:
    with assert_raises(WebhookParseError):
        _ = parse_gitlab(
            "Push Hook",
            {"ref": 123, "checkout_sha": "abc", "project": _GITLAB_PROJECT},
        )
