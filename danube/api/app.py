"""FastAPI application factory for the Danube Master.

`create_app` takes an already-open snekql `Database` and returns a wired FastAPI
app. The database is supplied by dependency injection (not a module global) so the
app can be exercised in-process against an in-memory database via
`httpx.ASGITransport`. Opening/closing the database is the caller's job; see
`danube.master` for the production wiring.

Scope here is read-only: health checks and job/pipeline reads. Auth, webhooks, the
RPC control plane, write endpoints, metrics, and SPA serving live in later issues.
"""

from fastapi import FastAPI
from snekql.sqlite import Database

from danube import __version__
from danube.api.deps import get_db
from danube.api.routes import health, jobs, pipelines

API_V1_PREFIX = "/api/v1"


def create_app(db: Database) -> FastAPI:
    """Build the FastAPI app, injecting ``db`` as the request-scoped database."""
    app = FastAPI(title="Danube Master API", version=__version__)

    def provide_db() -> Database:
        return db

    app.dependency_overrides[get_db] = provide_db

    app.include_router(health.router)
    app.include_router(jobs.router, prefix=API_V1_PREFIX)
    app.include_router(pipelines.router, prefix=API_V1_PREFIX)
    return app
