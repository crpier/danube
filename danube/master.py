"""Danube Master entrypoint.

Parses arguments, loads configuration, configures logging, opens the database, and
serves the FastAPI app with uvicorn at the configured bind address.
"""

import argparse
import logging
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import anyio
import uvicorn
from fastapi import FastAPI
from snekql.sqlite import Database

from danube import __version__
from danube.api import create_app
from danube.auth import AuthConfig
from danube.configsource import str_source
from danube.db import open_database
from danube.domain.limits import DEFAULT_CEILING, ResourceCeiling
from danube.observability import (
    Metrics,
    ObservabilityConfig,
    Tracer,
    configure_logging,
)
from danube.observability.logging import DEFAULT_LOG_LEVEL, LOG_LEVEL_ENV

logger = logging.getLogger("danube.master")

DEFAULT_BIND_ADDRESS = "127.0.0.1:8000"
DEFAULT_DATABASE_PATH = "danube.db"
DEFAULT_DATA_DIR = "/var/lib/danube"

BIND_ADDRESS_ENV = "DANUBE_BIND_ADDRESS"
DATA_DIR_ENV = "DANUBE_DATA_DIR"

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

    Built by `load_config` from the documented loading order: hardcoded defaults,
    then `danube.toml`, then environment variables (env wins). Only the fields the
    Master process consumes are parsed here; Blueprint-managed settings live in the
    config repository.
    """

    config_path: Path | None
    bind_address: str = DEFAULT_BIND_ADDRESS
    database_path: Path | str = DEFAULT_DATABASE_PATH
    data_dir: Path = Path(DEFAULT_DATA_DIR)
    log_level: str = DEFAULT_LOG_LEVEL
    observability: ObservabilityConfig = field(default_factory=ObservabilityConfig)
    auth: AuthConfig | None = None
    spa_dir: Path | None = None
    # Operator-set Resource Ceiling (`[limits]`): the `default` applied to a job's
    # unrequested limits and the `max` every request is clamped to (#53). Defaults
    # to the built-in conservative ceiling when the section is absent.
    limits: ResourceCeiling = DEFAULT_CEILING


def _read_config_file(config_path: Path | None) -> dict[str, Any]:
    """Parse the TOML config file, returning an empty mapping when absent."""
    if config_path is None:
        empty: dict[str, Any] = {}
        return empty
    return tomllib.loads(config_path.read_text(encoding="utf-8"))


def _section(table: dict[str, Any], name: str) -> dict[str, Any]:
    """Return the named sub-table, or an empty mapping when missing/non-table."""
    value = table.get(name)
    if isinstance(value, dict):
        return cast("dict[str, Any]", value)
    empty: dict[str, Any] = {}
    return empty


def load_config(config_path: Path | None) -> MasterConfig:
    """Load Master configuration following defaults -> `danube.toml` -> env.

    See `docs/configuration/server-config.md`. Environment variables override the
    file, which overrides the hardcoded defaults.
    """
    table = _read_config_file(config_path)
    server = _section(table, "server")
    database = _section(table, "database")
    logging_table = _section(table, "logging")
    bind_address = str_source(
        BIND_ADDRESS_ENV, server, "bind_address", default=DEFAULT_BIND_ADDRESS
    )
    data_dir = str_source(DATA_DIR_ENV, server, "data_dir", default=DEFAULT_DATA_DIR)
    database_path = str(database.get("path", DEFAULT_DATABASE_PATH))
    log_level = str_source(
        LOG_LEVEL_ENV, logging_table, "level", default=DEFAULT_LOG_LEVEL
    )
    return MasterConfig(
        config_path=config_path,
        bind_address=bind_address or DEFAULT_BIND_ADDRESS,
        database_path=database_path,
        data_dir=Path(data_dir or DEFAULT_DATA_DIR),
        log_level=log_level or DEFAULT_LOG_LEVEL,
        observability=ObservabilityConfig.from_sources(
            _section(table, "observability")
        ),
        auth=AuthConfig.from_sources(_section(table, "auth")),
        spa_dir=_resolve_spa_dir(),
        limits=ResourceCeiling.from_table(_section(table, "limits")),
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
    config = load_config(args.config)
    configure_logging(config.log_level)
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
