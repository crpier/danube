"""Unit tests for the `danube` CLI parsing and report formatting."""

from snektest import assert_eq, assert_raises, test

from danube.cli import build_parser, format_report
from danube.domain.runner_types import ReconcileReport


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
