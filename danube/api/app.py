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
from starlette.types import ASGIApp, Receive, Scope, Send

from danube import __version__
from danube.api.deps import (
    get_auth_config,
    get_db,
    get_job_manager,
    get_metrics,
    get_runner,
    get_tracer,
    get_webhook_config,
)
from danube.api.routes import (
    artifacts,
    control,
    health,
    jobs,
    pipelines,
    webhooks,
)
from danube.api.routes import (
    metrics as metrics_route,
)
from danube.auth import AuthConfig
from danube.observability import Metrics, Tracer
from danube.orchestrator import JobManager
from danube.rpc import ControlPlane
from danube.rpc import router as rpc_router
from danube.rpc.deps import get_control_plane
from danube.runner.base import Runner
from danube.webhooks import WebhookConfig

API_V1_PREFIX = "/api/v1"


class _TracingMiddleware:
    """Pure-ASGI middleware that wraps each HTTP request in a tracing span.

    Implemented as raw ASGI (not Starlette's `BaseHTTPMiddleware`) deliberately:
    `BaseHTTPMiddleware` spawns a per-request task group that interferes with the
    background job/DB lifecycle. A plain ASGI wrapper just brackets the downstream
    call, so it is a true no-op when the tracer is disabled.
    """

    def __init__(self, app: ASGIApp, tracer: Tracer) -> None:
        self._app = app
        self._tracer = tracer

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        method = scope.get("method", "")
        path = scope.get("path", "")
        with self._tracer.span(
            f"{method} {path}",
            **{"http.method": method, "http.target": path},
        ):
            await self._app(scope, receive, send)


def _wire_observability(
    app: FastAPI, metrics: Metrics, tracer: Tracer, runner: Runner | None
) -> None:
    """Inject the metrics/tracer dependencies, the trace middleware, and (when a
    runner is supplied) the readiness runner."""

    def provide_metrics() -> Metrics:
        return metrics

    def provide_tracer() -> Tracer:
        return tracer

    app.dependency_overrides[get_metrics] = provide_metrics
    app.dependency_overrides[get_tracer] = provide_tracer

    if runner is not None:

        def provide_runner() -> Runner | None:
            return runner

        app.dependency_overrides[get_runner] = provide_runner

    app.add_middleware(_TracingMiddleware, tracer=tracer)


def create_app(  # noqa: PLR0913, C901 - factory wiring each optional subsystem explicitly
    db: Database,
    control_plane: ControlPlane | None = None,
    job_manager: JobManager | None = None,
    webhook_config: WebhookConfig | None = None,
    *,
    metrics: Metrics | None = None,
    tracer: Tracer | None = None,
    runner: Runner | None = None,
    metrics_enabled: bool = True,
    auth: AuthConfig | None = None,
) -> FastAPI:
    """Build the FastAPI app, injecting `db` as the request-scoped database.

    Passing `control_plane` mounts the Coordinator RPC routes and injects it as
    the request-scoped control plane. Passing `job_manager` mounts the
    control-plane endpoints (trigger, cancel, log stream) and injects it the same
    way; without it the app is read-only. Passing `webhook_config` alongside a
    `job_manager` mounts the `/webhooks/*` ingestion routes; webhook triggers go
    through the same `JobManager`, so they share its dedup and concurrency cap.

    `metrics_enabled` (from `spec.observability`) gates the `/metrics` endpoint:
    when false the route is not mounted, so scrapes get a 404.

    Passing `auth` turns on OIDC/JWT authentication and team-based RBAC for the
    UI/API routes (read and control); without it the API is unauthenticated.
    Health and metrics endpoints are never gated by auth.
    """
    app = FastAPI(title="Danube Master API", version=__version__)

    def provide_db() -> Database:
        return db

    app.dependency_overrides[get_db] = provide_db
    if auth is not None:

        def provide_auth() -> AuthConfig | None:
            return auth

        app.dependency_overrides[get_auth_config] = provide_auth
    _wire_observability(app, metrics or Metrics(), tracer or Tracer(), runner)

    app.include_router(health.router)
    if metrics_enabled:
        app.include_router(metrics_route.router)
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
