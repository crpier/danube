"""The `danube` operator CLI.

Commands:

- `danube init`: scaffold the data directory, generate the AES-256 encryption key,
  and write a starter `danube.toml` (see `docs/deployment/installation.md`).
- `danube master`: run the Master process (delegates to `danube.master.main`).
- `danube runner reconcile`: compare the tracked `runner_state` rows with the
  live, labelled Podman resources and print the drift (see
  `LocalContainerRunner.reconcile`).
- `danube secret set KEY`: encrypt a secret value with AES-256-GCM and upsert it
  into the database (see `docs/architecture/security.md`, Secrets Management).

More commands are added as the appliance grows.
"""

from __future__ import annotations

import argparse
import inspect
import os
import sys
from pathlib import Path

import anyio

from danube import master as _master
from danube.db import open_database
from danube.domain.runner_types import ReconcileReport
from danube.runner.local import LocalContainerRunner
from danube.runner.podman import PodmanAdapter, build_async_client, default_socket_path
from danube.security import SecretCipher, generate_key, load_key, store_secret

DEFAULT_DATA_DIR = "/var/lib/danube"
DEFAULT_DATABASE_PATH = "danube.db"
DEFAULT_KEY_PATH = "/var/lib/danube/keys/encryption.key"
DEFAULT_CONFIG_PATH = "/etc/danube/danube.toml"

# Env overrides for the default data dir and config path, mirroring the Master's
# config-loading precedence (env wins over the hardcoded defaults). Lets a dev
# keep every runtime file in one local dir without retyping flags, e.g.:
#   export DANUBE_DATA_DIR=.danube DANUBE_CONFIG_PATH=.danube/danube.toml
# `DATA_DIR_ENV` is shared with `danube.master` so both read the same variable.
DATA_DIR_ENV = _master.DATA_DIR_ENV
CONFIG_PATH_ENV = "DANUBE_CONFIG_PATH"


def _default_data_dir() -> Path:
    """The `init`/`reconcile` data-dir default: env override, else the builtin."""
    return Path(os.environ.get(DATA_DIR_ENV) or DEFAULT_DATA_DIR)


def _default_config_path() -> Path:
    """The `init` config-path default: env override, else the builtin."""
    return Path(os.environ.get(CONFIG_PATH_ENV) or DEFAULT_CONFIG_PATH)


def _env_config_override() -> Path | None:
    """The `master --config` default: the env path when set, else `None`.

    Returning `None` keeps the existing behavior of running on built-in defaults
    rather than reading a possibly-absent `/etc/danube/danube.toml`.
    """
    value = os.environ.get(CONFIG_PATH_ENV)
    return Path(value) if value else None


# Sub-directories created under the data dir by `danube init`.
_DATA_SUBDIRS = ("keys", "logs", "artifacts", "workspaces")

_REPORT_SECTIONS: tuple[tuple[str, str], ...] = (
    ("stale_pods", "pods for finished jobs"),
    ("orphaned_containers", "containers with no tracked state"),
    ("stale_workspaces", "workspaces for inactive jobs"),
    ("missing_pods", "running jobs with no pod"),
    ("failed_cleanups", "jobs with a failed cleanup"),
)


async def _run_reconcile(args: argparse.Namespace) -> int:
    socket_path: Path = args.socket
    client = build_async_client(socket_path)
    db = await open_database(args.database)
    try:
        runner = LocalContainerRunner(PodmanAdapter(client), args.data_dir, db=db)
        report = await runner.reconcile()
        print(format_report(report))
    finally:
        await client.aclose()
        await db.close()
    return 0


async def _run_secret_set(args: argparse.Namespace) -> int:
    key_path: Path = args.key_file
    cipher = SecretCipher(load_key(key_path))
    value: str = args.value if args.value is not None else sys.stdin.read().rstrip("\n")
    db = await open_database(args.database)
    try:
        secret_id = await store_secret(
            db,
            cipher,
            key=args.key,
            value=value,
            pipeline_id=args.pipeline,
        )
    finally:
        await db.close()
    scope = "global" if args.pipeline is None else f"pipeline {args.pipeline}"
    print(f"Stored secret {args.key!r} ({scope}) as {secret_id}.")
    return 0


def render_config_template(data_dir: Path) -> str:
    """Render a starter `danube.toml` body wired to `data_dir`.

    Mirrors `docs/configuration/server-config.md`. Operator-tunable blocks
    (`config_repo`, `webhooks`) are commented out so the appliance starts with a
    safe, unauthenticated, single-host default that an operator opts out of.
    """
    return f"""\
# Danube Master configuration. See docs/configuration/server-config.md.
# Loading order: defaults -> this file -> environment variables (env wins).

[server]
bind_address = "0.0.0.0:8080"
data_dir = "{data_dir}"

[database]
path = "{data_dir}/danube.db"

[logging]
level = "info"

[observability]
metrics_enabled = true
traces_enabled = false
otel_endpoint = ""

[auth]
# Disabled by default. Set enabled = true and supply an hs256_secret (or an
# RS256 public_key) to require a valid JWT on the read/control endpoints.
enabled = false

# [config_repo]
# url = "git@github.com:myorg/danube-blueprint.git"
# branch = "main"
# sync_interval = "60s"
# ssh_key_path = "{data_dir}/keys/git_deploy_key"
"""


def _run_init(args: argparse.Namespace) -> int:
    data_dir: Path = args.data_dir
    config_path: Path = args.config
    force: bool = args.force

    data_dir.mkdir(parents=True, exist_ok=True)
    for sub in _DATA_SUBDIRS:
        (data_dir / sub).mkdir(parents=True, exist_ok=True)
    # The keys directory holds raw secret material; keep it owner-only.
    (data_dir / "keys").chmod(0o700)

    key_path = data_dir / "keys" / "encryption.key"
    if key_path.exists() and not force:
        print(f"Encryption key already exists at {key_path}; leaving it untouched.")
    else:
        # Never overwrite without --force: a new key orphans every stored secret.
        _ = key_path.write_bytes(generate_key())
        key_path.chmod(0o600)
        print(f"Wrote encryption key to {key_path}.")

    config_path.parent.mkdir(parents=True, exist_ok=True)
    if config_path.exists() and not force:
        print(f"Config already exists at {config_path}; leaving it untouched.")
    else:
        _ = config_path.write_text(render_config_template(data_dir), encoding="utf-8")
        config_path.chmod(0o600)
        print(f"Wrote starter config to {config_path}.")

    print("Danube initialised. Review the config, then start the Master with:")
    print(f"  danube master --config {config_path}")
    return 0


def _run_master(args: argparse.Namespace) -> int:
    """Delegate to `danube.master.main`, forwarding the config path.

    This is a synchronous handler: `danube.master.main` runs its own event loop,
    so it must not be dispatched through `anyio.run` (no nested loops).
    """
    forwarded: list[str] = []
    config: Path | None = args.config
    if config is not None:
        forwarded.extend(["--config", str(config)])
    return _master.main(forwarded)


def format_report(report: ReconcileReport) -> str:
    """Render a `ReconcileReport` as human-readable operator output."""
    dump = report.model_dump()
    lines: list[str] = []
    total = 0
    for field_name, description in _REPORT_SECTIONS:
        entries: list[str] = dump[field_name]
        total += len(entries)
        lines.append(f"{description} ({field_name}): {len(entries)}")
        lines.extend(f"  - {entry}" for entry in entries)
    header = "No drift detected." if total == 0 else f"Found {total} discrepancies."
    return "\n".join([header, *lines])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="danube", description="Danube operator CLI.")
    subcommands = parser.add_subparsers(dest="group", required=True)

    init = subcommands.add_parser(
        "init",
        help="Scaffold the data directory, encryption key, and starter config.",
    )
    _ = init.add_argument(
        "--data-dir",
        type=Path,
        default=_default_data_dir(),
        help=f"Danube data directory to create and populate (env: {DATA_DIR_ENV}).",
    )
    _ = init.add_argument(
        "--config",
        type=Path,
        default=_default_config_path(),
        help=f"Path to write the starter danube.toml (env: {CONFIG_PATH_ENV}).",
    )
    _ = init.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing key and config. WARNING: a new key orphans every stored secret.",
    )
    init.set_defaults(handler=_run_init)

    master = subcommands.add_parser(
        "master",
        help="Run the Danube Master process.",
    )
    _ = master.add_argument(
        "--config",
        type=Path,
        default=_env_config_override(),
        help=(
            "Path to the Master configuration file (danube.toml) "
            f"(env: {CONFIG_PATH_ENV})."
        ),
    )
    master.set_defaults(handler=_run_master)

    runner = subcommands.add_parser("runner", help="Runner operations.")
    runner_commands = runner.add_subparsers(dest="command", required=True)

    reconcile = runner_commands.add_parser(
        "reconcile",
        help="Report drift between tracked state and live Podman resources.",
    )
    _ = reconcile.add_argument(
        "--socket",
        type=Path,
        default=default_socket_path(),
        help="Path to the rootless Podman API socket.",
    )
    _ = reconcile.add_argument(
        "--database",
        type=Path,
        default=Path(DEFAULT_DATABASE_PATH),
        help="Path to the Danube SQLite database.",
    )
    _ = reconcile.add_argument(
        "--data-dir",
        type=Path,
        default=_default_data_dir(),
        help=(
            f"Danube data directory holding per-job workspaces (env: {DATA_DIR_ENV})."
        ),
    )
    reconcile.set_defaults(handler=_run_reconcile)

    secret = subcommands.add_parser("secret", help="Secret management.")
    secret_commands = secret.add_subparsers(dest="command", required=True)

    secret_set = secret_commands.add_parser(
        "set",
        help="Encrypt and store a secret value (AES-256-GCM).",
    )
    _ = secret_set.add_argument("key", help="The secret key (name).")
    _ = secret_set.add_argument(
        "--value",
        default=None,
        help="The secret value. Read from stdin when omitted (avoids the process table).",
    )
    _ = secret_set.add_argument(
        "--pipeline",
        default=None,
        help="Scope the secret to this pipeline id. Omit for a global secret.",
    )
    _ = secret_set.add_argument(
        "--key-file",
        dest="key_file",
        type=Path,
        default=Path(DEFAULT_KEY_PATH),
        help="Path to the AES-256 encryption key file.",
    )
    _ = secret_set.add_argument(
        "--database",
        type=Path,
        default=Path(DEFAULT_DATABASE_PATH),
        help="Path to the Danube SQLite database.",
    )
    secret_set.set_defaults(handler=_run_secret_set)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handler = args.handler
    # Async handlers run on a fresh event loop; sync handlers (e.g. `master`,
    # which starts its own loop) are called directly to avoid nesting loops.
    if inspect.iscoroutinefunction(handler):
        return anyio.run(handler, args)
    return handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
