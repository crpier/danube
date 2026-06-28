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
from danube.api.deps import get_db, get_job_manager, get_webhook_config
from danube.api.routes import artifacts, control, health, jobs, pipelines, webhooks
from danube.orchestrator import JobManager
from danube.rpc import ControlPlane
from danube.rpc import router as rpc_router
from danube.rpc.deps import get_control_plane
from danube.webhooks import WebhookConfig

API_V1_PREFIX = "/api/v1"


def create_app(
    db: Database,
    control_plane: ControlPlane | None = None,
    job_manager: JobManager | None = None,
    webhook_config: WebhookConfig | None = None,
) -> FastAPI:
    """Build the FastAPI app, injecting `db` as the request-scoped database.

    Passing `control_plane` mounts the Coordinator RPC routes and injects it as
    the request-scoped control plane. Passing `job_manager` mounts the
    control-plane endpoints (trigger, cancel, log stream) and injects it the same
    way; without it the app is read-only. Passing `webhook_config` alongside a
    `job_manager` mounts the `/webhooks/*` ingestion routes; webhook triggers go
    through the same `JobManager`, so they share its dedup and concurrency cap.
    """
    app = FastAPI(title="Danube Master API", version=__version__)

    def provide_db() -> Database:
        return db

    app.dependency_overrides[get_db] = provide_db

    app.include_router(health.router)
    app.include_router(jobs.router, prefix=API_V1_PREFIX)
    app.include_router(pipelines.router, prefix=API_V1_PREFIX)
    app.include_router(artifacts.router, prefix=API_V1_PREFIX)

    if control_plane is not None:

        def provide_control_plane() -> ControlPlane:
            return control_plane

        app.dependency_overrides[get_control_plane] = provide_control_plane
        app.include_router(rpc_router)

    if job_manager is not None:

        def provide_job_manager() -> JobManager:
            return job_manager

        app.dependency_overrides[get_job_manager] = provide_job_manager
        app.include_router(control.router, prefix=API_V1_PREFIX)

        if webhook_config is not None:

            def provide_webhook_config() -> WebhookConfig:
                return webhook_config

            app.dependency_overrides[get_webhook_config] = provide_webhook_config
            app.include_router(webhooks.router)
    return app
