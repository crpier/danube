"""Danube Master entrypoint.

Parses arguments, loads configuration, configures logging, and logs startup.
No HTTP server is started yet; that arrives in a later issue.
"""

import argparse
import logging
from dataclasses import dataclass
from pathlib import Path

from danube import __version__

logger = logging.getLogger("danube.master")


@dataclass(frozen=True)
class MasterConfig:
    """Loaded Master configuration.

    This is a stub: it records the config path without parsing its contents.
    """

    config_path: Path | None


def load_config(config_path: Path | None) -> MasterConfig:
    """Load Master configuration from the given path.

    Stubbed for now: only the path is retained. Real parsing arrives with the
    server-config issue.
    """
    return MasterConfig(config_path=config_path)


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
    logger.info("Danube Master ready")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
