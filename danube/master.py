"""Danube Master entrypoint.

Parses arguments, loads configuration, configures logging, opens the database, and
serves the FastAPI app with uvicorn at the configured bind address.
"""

import argparse
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

import anyio
import uvicorn
from fastapi import FastAPI
from snekql.sqlite import Database

from danube import __version__
from danube.api import create_app
from danube.auth import AuthConfig
from danube.db import open_database
from danube.observability import (
    Metrics,
    ObservabilityConfig,
    Tracer,
    configure_logging,
)

logger = logging.getLogger("danube.master")

# Defaults used until the server-config issue adds real config-file parsing.
DEFAULT_BIND_ADDRESS = "127.0.0.1:8000"
DEFAULT_DATABASE_PATH = "danube.db"

# Where the built frontend SPA is served from. Overridable so a packaged deploy
# can point at wherever its build lands; the default is the repo's `frontend/dist`
# (two levels up from this module: `danube/master.py` -> repo root).
_SPA_DIR_ENV = "DANUBE_FRONTEND_DIST"
_DEFAULT_SPA_DIR = Path(__file__).resolve().parent.parent / "frontend" / "dist"


def _resolve_spa_dir() -> Path | None:
    """Pick the SPA directory from the environment, falling back to the repo build.

    Returns `None` only when neither an override nor the default directory exists,
    so the Master runs API-only without a frontend build present.
    """
    override = os.environ.get(_SPA_DIR_ENV)
    candidate = Path(override) if override else _DEFAULT_SPA_DIR
    return candidate if candidate.is_dir() else None


@dataclass(frozen=True)
class MasterConfig:
    """Loaded Master configuration.

    This is a stub: it records the config path but does not yet parse its
    contents, so `bind_address` and `database_path` fall back to defaults. Real
    parsing arrives with the server-config issue.
    """

    config_path: Path | None
    bind_address: str = DEFAULT_BIND_ADDRESS
    database_path: Path | str = DEFAULT_DATABASE_PATH
    observability: ObservabilityConfig = field(default_factory=ObservabilityConfig)
    auth: AuthConfig | None = None
    spa_dir: Path | None = None


def load_config(config_path: Path | None) -> MasterConfig:
    """Load Master configuration from the given path.

    Stubbed for now: only the path is retained and defaults are used for the
    bind address and database location. Observability and auth toggles come from
    the environment until full server-config parsing arrives.
    """
    return MasterConfig(
        config_path=config_path,
        observability=ObservabilityConfig.from_env(),
        auth=AuthConfig.from_env(),
        spa_dir=_resolve_spa_dir(),
    )


def _split_bind_address(bind_address: str) -> tuple[str, int]:
    """Split a `host:port` bind address into its parts.

    Raises `ValueError` if the port is missing or not an integer.
    """
    host, separator, port = bind_address.rpartition(":")
    if not separator:
        msg = f"bind_address {bind_address!r} must be in 'host:port' form"
        raise ValueError(msg)
    return host, int(port)


async def build_app(config: MasterConfig) -> tuple[FastAPI, Database]:
    """Open the database and build the FastAPI app for ``config``.

    Returns the app together with the open database so the caller controls the
    database lifetime (and can close it on shutdown).
    """
    db = await open_database(config.database_path)
    if config.auth is None:
        logger.warning(
            "master_auth_disabled",
            extra={"event": "master_auth_disabled"},
        )
    tracer = Tracer(
        enabled=config.observability.traces_enabled,
        endpoint=config.observability.otel_endpoint,
    )
    return (
        create_app(
            db,
            metrics=Metrics(),
            tracer=tracer,
            metrics_enabled=config.observability.metrics_enabled,
            auth=config.auth,
            spa_dir=config.spa_dir,
        ),
        db,
    )


async def serve(config: MasterConfig) -> None:
    """Serve the app with uvicorn, closing the database on shutdown."""
    host, port = _split_bind_address(config.bind_address)
    app, db = await build_app(config)
    try:
        server = uvicorn.Server(uvicorn.Config(app, host=host, port=port))
        logger.info(
            "master_serving",
            extra={"event": "master_serving", "bind_address": config.bind_address},
        )
        await server.serve()
    finally:
        await db.close()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="danube.master",
        description="Run the Danube Master process.",
    )
    _ = parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to the Master configuration file.",
    )
    _ = parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    configure_logging()
    config = load_config(args.config)
    logger.info(
        "master_starting", extra={"event": "master_starting", "version": __version__}
    )
    if config.config_path is None:
        logger.info(
            "master_no_config_file",
            extra={"event": "master_no_config_file"},
        )
    else:
        logger.info(
            "master_config_loaded",
            extra={
                "event": "master_config_loaded",
                "config_path": str(config.config_path),
            },
        )
    anyio.run(serve, config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
