"""Danube Master entrypoint.

Parses arguments, loads configuration, configures logging, opens the database, and
serves the FastAPI app with uvicorn at the configured bind address.
"""

import argparse
import logging
from dataclasses import dataclass
from pathlib import Path

import anyio
import uvicorn
from fastapi import FastAPI
from snekql.sqlite import Database

from danube import __version__
from danube.api import create_app
from danube.db import open_database

logger = logging.getLogger("danube.master")

# Defaults used until the server-config issue adds real config-file parsing.
DEFAULT_BIND_ADDRESS = "127.0.0.1:8000"
DEFAULT_DATABASE_PATH = "danube.db"


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


def load_config(config_path: Path | None) -> MasterConfig:
    """Load Master configuration from the given path.

    Stubbed for now: only the path is retained and defaults are used for the
    bind address and database location. Real parsing arrives with the
    server-config issue.
    """
    return MasterConfig(config_path=config_path)


def _split_bind_address(bind_address: str) -> tuple[str, int]:
    """Split a ``host:port`` bind address into its parts.

    Raises ``ValueError`` if the port is missing or not an integer.
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
    return create_app(db), db


async def serve(config: MasterConfig) -> None:
    """Serve the app with uvicorn, closing the database on shutdown."""
    host, port = _split_bind_address(config.bind_address)
    app, db = await build_app(config)
    try:
        server = uvicorn.Server(uvicorn.Config(app, host=host, port=port))
        logger.info("Serving Danube Master API on %s", config.bind_address)
        await server.serve()
    finally:
        await db.close()


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


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
    logger.info("Danube Master %s starting", __version__)
    if config.config_path is None:
        logger.info("No config file provided; using defaults")
    else:
        logger.info("Loaded config from %s", config.config_path)
    anyio.run(serve, config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
