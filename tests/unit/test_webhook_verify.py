"""Tests for webhook authentication: GitHub HMAC signatures and GitLab tokens."""

import hashlib
import hmac

from snektest import assert_eq, test

from danube.webhooks import verify_github_signature, verify_gitlab_token

_SECRET = "s3cr3t"


def _github_signature(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


@test(mark="fast")
def test_github_valid_signature_accepted() -> None:
    body = b'{"ref": "refs/heads/main"}'

    assert_eq(
        verify_github_signature(_SECRET, body, _github_signature(_SECRET, body)),
        True,
    )


@test(mark="fast")
def test_github_wrong_secret_rejected() -> None:
    body = b'{"ref": "refs/heads/main"}'

    assert_eq(
        verify_github_signature(_SECRET, body, _github_signature("other", body)),
        False,
    )


@test(mark="fast")
def test_github_tampered_body_rejected() -> None:
    signature = _github_signature(_SECRET, b"original")

    assert_eq(verify_github_signature(_SECRET, b"tampered", signature), False)


@test(mark="fast")
def test_github_missing_header_rejected() -> None:
    assert_eq(verify_github_signature(_SECRET, b"body", None), False)


@test(mark="fast")
def test_gitlab_matching_token_accepted() -> None:
    assert_eq(verify_gitlab_token(_SECRET, _SECRET), True)


@test(mark="fast")
def test_gitlab_wrong_token_rejected() -> None:
    assert_eq(verify_gitlab_token(_SECRET, "nope"), False)


@test(mark="fast")
def test_gitlab_missing_header_rejected() -> None:
    assert_eq(verify_gitlab_token(_SECRET, None), False)
