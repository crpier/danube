"""Unit tests for the `danube` CLI parsing and report formatting."""

import argparse
import tempfile
from pathlib import Path

from snekql.sqlite import insert, select
from snektest import assert_eq, assert_raises, test

from danube.cli import _run_secret_set, build_parser, format_report
from danube.db import open_database
from danube.db.models import Pipeline, Secret, Team
from danube.domain.runner_types import ReconcileReport
from danube.security import SecretCipher, generate_key


@test(mark="fast")
def test_format_report_clean() -> None:
    output = format_report(ReconcileReport())
    assert output.startswith("No drift detected.")


@test(mark="fast")
def test_format_report_lists_discrepancies() -> None:
    report = ReconcileReport(
        stale_pods=["danube-job-a"],
        missing_pods=["b", "c"],
    )

    output = format_report(report)

    assert output.startswith("Found 3 discrepancies.")
    assert "  - danube-job-a" in output
    assert "  - b" in output
    assert "  - c" in output


@test(mark="fast")
def test_parser_requires_a_subcommand() -> None:
    parser = build_parser()
    with assert_raises(SystemExit):
        _ = parser.parse_args([])


@test(mark="fast")
def test_parser_reconcile_defaults_and_overrides() -> None:
    parser = build_parser()
    args = parser.parse_args(["runner", "reconcile", "--data-dir", "/srv/danube"])
    assert_eq(str(args.data_dir), "/srv/danube")
    assert args.handler is not None


@test(mark="fast")
def test_parser_secret_set_defaults_and_overrides() -> None:
    parser = build_parser()
    args = parser.parse_args(
        ["secret", "set", "API_KEY", "--value", "v", "--pipeline", "p1"]
    )
    assert_eq(args.key, "API_KEY")
    assert_eq(args.value, "v")
    assert_eq(args.pipeline, "p1")
    assert_eq(str(args.key_file), "/var/lib/danube/keys/encryption.key")
    assert args.handler is not None


@test(mark="fast")
def test_parser_secret_set_requires_key() -> None:
    parser = build_parser()
    with assert_raises(SystemExit):
        _ = parser.parse_args(["secret", "set"])


@test(mark="medium")
async def test_secret_set_encrypts_and_stores_value() -> None:
    with tempfile.TemporaryDirectory() as raw_dir:
        directory = Path(raw_dir)
        key = generate_key()
        key_path = directory / "encryption.key"
        key_path.write_bytes(key)
        db_path = directory / "danube.db"

        # Seed the schema and a pipeline so the FK and scoping resolve.
        db = await open_database(db_path)
        async with db.transaction() as tx:
            await tx.execute(insert(Team(id="t1", name="team", global_admin=False)))
            await tx.execute(
                insert(
                    Pipeline(
                        id="p1",
                        name="p1",
                        team_id="t1",
                        repo_url="https://example.test/p1.git",
                        worker_image="img:p1",
                    )
                )
            )
        await db.close()

        args = argparse.Namespace(
            key="DEPLOY_TOKEN",
            value="tok-123",
            pipeline="p1",
            key_file=key_path,
            database=db_path,
        )
        code = await _run_secret_set(args)
        assert_eq(code, 0)

        db = await open_database(db_path)
        try:
            async with db.transaction() as tx:
                row = await tx.fetch_one(
                    select(Secret).where(Secret.key.eq("DEPLOY_TOKEN"))
                )
        finally:
            await db.close()
        assert row.value_encrypted != b"tok-123"
        assert_eq(SecretCipher(key).decrypt(row.value_encrypted), "tok-123")
