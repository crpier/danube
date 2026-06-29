"""Unit tests for the `danube master` subcommand.

The subcommand delegates to `danube.master.main`, which runs its own event loop,
so the CLI must dispatch it synchronously (not through `anyio.run`).
"""

from snektest import assert_eq, test

import danube.master as master_mod
from danube.cli import build_parser, main


@test(mark="fast")
def test_parser_master_forwards_config() -> None:
    parser = build_parser()
    args = parser.parse_args(["master", "--config", "/etc/danube/danube.toml"])
    assert_eq(str(args.config), "/etc/danube/danube.toml")
    assert args.handler is not None


@test(mark="medium")
def test_master_subcommand_delegates_to_master_main() -> None:
    captured: dict[str, object] = {}

    def fake_main(argv: list[str] | None = None) -> int:
        captured["argv"] = argv
        return 7

    original = master_mod.main
    master_mod.main = fake_main
    try:
        code = main(["master", "--config", "/etc/danube/danube.toml"])
    finally:
        master_mod.main = original

    assert_eq(code, 7)
    assert_eq(captured["argv"], ["--config", "/etc/danube/danube.toml"])


@test(mark="medium")
def test_master_subcommand_without_config_forwards_empty() -> None:
    captured: dict[str, object] = {}

    def fake_main(argv: list[str] | None = None) -> int:
        captured["argv"] = argv
        return 0

    original = master_mod.main
    master_mod.main = fake_main
    try:
        code = main(["master"])
    finally:
        master_mod.main = original

    assert_eq(code, 0)
    assert_eq(captured["argv"], [])
