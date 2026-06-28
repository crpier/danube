"""Coordinator <-> Master RPC control plane (server side).

`ControlPlane` owns active job sessions and mediates every Coordinator request
(run a step, fetch a secret, upload an artifact, report status). The FastAPI
router in `danube.rpc.routes` exposes it over HTTP/JSON; the Coordinator SDK in
`danube.sdk` is the client.
"""

from danube.rpc.control_plane import (
    ArtifactSourceError,
    ControlPlane,
    InvalidTokenError,
    JobSession,
    RpcError,
    SecretNotAuthorizedError,
    SessionNotActiveError,
    scrub_secrets,
)
from danube.rpc.routes import router

__all__ = [
    "ArtifactSourceError",
    "ControlPlane",
    "InvalidTokenError",
    "JobSession",
    "RpcError",
    "SecretNotAuthorizedError",
    "SessionNotActiveError",
    "router",
    "scrub_secrets",
]
