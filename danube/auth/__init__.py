"""Authentication and authorization for the Danube UI/API.

OIDC/JWT authentication (`config`, `jwt`) maps callers onto Blueprint
users/teams (`principal`) and enforces team-based RBAC on the read and control
endpoints. This is the UI/API auth surface only; the Coordinator RPC path keeps
its separate job-scoped token auth (`danube.rpc`).
"""

from danube.auth.config import AuthConfig, AuthConfigError
from danube.auth.jwt import (
    Claims,
    ExpiredTokenError,
    InvalidClaimError,
    InvalidSignatureError,
    InvalidTokenError,
    JwtError,
    encode,
    verify,
)
from danube.auth.principal import (
    Principal,
    pipeline_permission_ok,
    readable_pipeline_ids,
    resolve_principal,
)

__all__ = [
    "AuthConfig",
    "AuthConfigError",
    "Claims",
    "ExpiredTokenError",
    "InvalidClaimError",
    "InvalidSignatureError",
    "InvalidTokenError",
    "JwtError",
    "Principal",
    "encode",
    "pipeline_permission_ok",
    "readable_pipeline_ids",
    "resolve_principal",
    "verify",
]
