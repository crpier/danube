"""FastAPI dependencies for the RPC control plane.

The `ControlPlane` is injected (never a module global) so the app stays testable
against an in-memory database and a `FakeRunner`: `create_app` registers a
`dependency_overrides` entry for `get_control_plane`. The bare key fails loudly if
the RPC routes are mounted without a control plane wired in.

The job-scoped token travels in the `Authorization: Bearer` header so it never
appears in a logged request body.
"""

from typing import Annotated

from fastapi import Depends, Header, HTTPException, status

from danube.rpc.control_plane import ControlPlane


def get_control_plane() -> ControlPlane:
    """Dependency key for the request-scoped control plane.

    Always overridden via `app.dependency_overrides` in `create_app`; reaching
    this body means the RPC routes were mounted without a control plane.
    """
    msg = "control plane dependency is not configured; build the app with create_app"
    raise RuntimeError(msg)


ControlPlaneDep = Annotated[ControlPlane, Depends(get_control_plane)]


def bearer_token(
    authorization: Annotated[str | None, Header()] = None,
) -> str:
    """Extract the job-scoped bearer token from the `Authorization` header."""
    prefix = "Bearer "
    if authorization is None or not authorization.startswith(prefix):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing or malformed bearer token",
        )
    return authorization.removeprefix(prefix)


TokenDep = Annotated[str, Depends(bearer_token)]
