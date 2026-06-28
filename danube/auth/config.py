"""Authentication configuration for the UI/API.

`AuthConfig` carries the JWT validation parameters documented in
`docs/architecture/security.md` (Authentication) and `docs/configuration/server-config.md`
(`[auth]`): the expected issuer and audience, the signing algorithm, and the key
material used to verify signatures.

Two algorithm families are supported:

- ``HS256`` -- a symmetric shared secret. This doubles as the *embedded IdP* mode
  for single-host setups: Danube both mints (see `danube.auth.jwt.encode`) and
  verifies tokens with the same secret.
- ``RS256`` -- an asymmetric RSA public key, for an external OIDC provider whose
  public key (PEM) is provisioned into the appliance configuration.

When no `AuthConfig` is wired into `create_app`, the API runs unauthenticated;
this keeps the read-only/in-process test apps simple and is the documented
opt-in behaviour.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

Algorithm = Literal["HS256", "RS256"]

AUTH_ENABLED_ENV = "DANUBE_AUTH_ENABLED"
AUTH_ISSUER_ENV = "DANUBE_AUTH_ISSUER"
AUTH_AUDIENCE_ENV = "DANUBE_AUTH_AUDIENCE"
AUTH_ALGORITHM_ENV = "DANUBE_AUTH_ALGORITHM"
AUTH_HS256_SECRET_ENV = "DANUBE_AUTH_HS256_SECRET"  # noqa: S105 - env var name, not a secret
AUTH_PUBLIC_KEY_ENV = "DANUBE_AUTH_PUBLIC_KEY"
AUTH_LEEWAY_SECONDS_ENV = "DANUBE_AUTH_LEEWAY_SECONDS"


class AuthConfigError(ValueError):
    """Raised when the auth configuration is internally inconsistent."""


@dataclass(frozen=True, slots=True)
class AuthConfig:
    """Validated JWT parameters for protecting the UI/API.

    `secret` is required for ``HS256``; `public_key_pem` is required for
    ``RS256``. `leeway_seconds` is the clock-skew tolerance applied to the
    expiry check.
    """

    issuer: str
    audience: str
    algorithm: Algorithm = "HS256"
    secret: str | None = None
    public_key_pem: str | None = None
    leeway_seconds: int = 0

    def __post_init__(self) -> None:
        if self.algorithm == "HS256" and not self.secret:
            msg = "HS256 auth requires a shared secret"
            raise AuthConfigError(msg)
        if self.algorithm == "RS256" and not self.public_key_pem:
            msg = "RS256 auth requires a public key (PEM)"
            raise AuthConfigError(msg)

    @classmethod
    def from_env(cls) -> AuthConfig | None:
        """Build an `AuthConfig` from the environment, or `None` when disabled.

        Returns `None` unless ``DANUBE_AUTH_ENABLED`` is truthy, so the appliance
        runs unauthenticated by default until an operator opts in. The public key
        may be supplied inline (PEM text) or via a file path.
        """
        if os.environ.get(AUTH_ENABLED_ENV, "").strip().lower() not in {
            "1",
            "true",
            "yes",
            "on",
        }:
            return None
        algorithm: Algorithm = (
            "RS256"
            if os.environ.get(AUTH_ALGORITHM_ENV, "HS256").upper() == "RS256"
            else "HS256"
        )
        public_key = os.environ.get(AUTH_PUBLIC_KEY_ENV)
        if public_key is not None:
            candidate = Path(public_key)
            if candidate.is_file():
                public_key = candidate.read_text(encoding="utf-8")
        leeway_raw = os.environ.get(AUTH_LEEWAY_SECONDS_ENV, "0")
        return cls(
            issuer=os.environ.get(AUTH_ISSUER_ENV, ""),
            audience=os.environ.get(AUTH_AUDIENCE_ENV, ""),
            algorithm=algorithm,
            secret=os.environ.get(AUTH_HS256_SECRET_ENV),
            public_key_pem=public_key,
            leeway_seconds=int(leeway_raw) if leeway_raw.isdigit() else 0,
        )
