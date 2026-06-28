"""Configuration for inbound Git webhook authentication.

The Master authenticates webhooks with a per-provider shared secret supplied at
startup (not stored per-pipeline): GitHub signs the request body with an HMAC, and
GitLab echoes a plaintext token header. A `None` value disables that provider's
endpoint — every request is rejected, because it cannot be authenticated.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class WebhookConfig:
    """Secrets used to authenticate inbound Git webhooks.

    ``github_secret`` is the HMAC-SHA256 shared secret configured on the GitHub
    webhook; ``gitlab_token`` is the plaintext token GitLab echoes in the
    ``X-Gitlab-Token`` header. Either may be ``None`` to disable that provider.
    """

    github_secret: str | None = None
    gitlab_token: str | None = None
