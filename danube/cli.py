"""The `danube` operator CLI.

Commands:

- `danube runner reconcile`: compare the tracked `runner_state` rows with the
  live, labelled Podman resources and print the drift (see
  `LocalContainerRunner.reconcile`).
- `danube secret set KEY`: encrypt a secret value with AES-256-GCM and upsert it
  into the database (see `docs/architecture/security.md`, Secrets Management).

More commands are added as the appliance grows.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import anyio

from danube.db import open_database
from danube.domain.runner_types import ReconcileReport
from danube.runner.local import LocalContainerRunner
from danube.runner.podman import PodmanAdapter, build_async_client, default_socket_path
from danube.security import SecretCipher, load_key, store_secret

DEFAULT_DATA_DIR = "/var/lib/danube"
DEFAULT_DATABASE_PATH = "danube.db"
DEFAULT_KEY_PATH = "/var/lib/danube/keys/encryption.key"

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
        default=Path(DEFAULT_DATA_DIR),
        help="Danube data directory holding per-job workspaces.",
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
    return anyio.run(args.handler, args)


if __name__ == "__main__":
    raise SystemExit(main())
