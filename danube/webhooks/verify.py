"""Authenticate inbound webhooks against the configured per-provider secret.

GitHub signs the raw request body with HMAC-SHA256 and sends the hex digest in
`X-Hub-Signature-256` as `sha256=<digest>`; GitLab does not sign and instead
echoes a shared token in `X-Gitlab-Token`. Both comparisons use
`hmac.compare_digest` so a mismatch leaks no timing information, and a missing
header is treated as a failed verification rather than an error.
"""

from __future__ import annotations

import hashlib
import hmac

_GITHUB_SIGNATURE_PREFIX = "sha256="


def verify_github_signature(
    secret: str, body: bytes, signature_header: str | None
) -> bool:
    """Return whether `signature_header` is a valid HMAC-SHA256 of `body`."""
    if not signature_header:
        return False
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    expected = f"{_GITHUB_SIGNATURE_PREFIX}{digest}"
    return hmac.compare_digest(expected, signature_header)


def verify_gitlab_token(token: str, token_header: str | None) -> bool:
    """Return whether `token_header` matches the configured GitLab token."""
    if token_header is None:
        return False
    return hmac.compare_digest(token, token_header)
