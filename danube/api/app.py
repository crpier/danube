"""FastAPI application factory for the Danube Master.

`create_app` takes an already-open snekql `Database` and returns a wired FastAPI
app. The database is supplied by dependency injection (not a module global) so the
app can be exercised in-process against an in-memory database via
`httpx.ASGITransport`. Opening/closing the database is the caller's job; see
`danube.master` for the production wiring.

Scope here is read-only HTTP plus the Coordinator RPC control plane. When a
`ControlPlane` is supplied, the `/rpc/*` routes are mounted and the control plane
is injected the same way the database is. Webhooks, write endpoints, metrics, and
SPA serving live in later issues.
"""

from fastapi import FastAPI
from snekql.sqlite import Database

from danube import __version__
from danube.api.deps import get_db
from danube.api.routes import health, jobs, pipelines
from danube.rpc import ControlPlane
from danube.rpc import router as rpc_router
from danube.rpc.deps import get_control_plane

API_V1_PREFIX = "/api/v1"


def create_app(db: Database, control_plane: ControlPlane | None = None) -> FastAPI:
    """Build the FastAPI app, injecting ``db`` as the request-scoped database.

    Passing ``control_plane`` mounts the Coordinator RPC routes and injects it as
    the request-scoped control plane.
    """
    app = FastAPI(title="Danube Master API", version=__version__)

    def provide_db() -> Database:
        return db

    app.dependency_overrides[get_db] = provide_db

    app.include_router(health.router)
    app.include_router(jobs.router, prefix=API_V1_PREFIX)
    app.include_router(pipelines.router, prefix=API_V1_PREFIX)

    if control_plane is not None:

        def provide_control_plane() -> ControlPlane:
            return control_plane

        app.dependency_overrides[get_control_plane] = provide_control_plane
        app.include_router(rpc_router)
    return app
