"""Unit tests for the `danube init` bootstrap command.

`danube init` scaffolds the data directory, generates the encryption key, and
writes a starter `danube.toml`. It must be idempotent and must never silently
overwrite an existing encryption key (that would orphan every stored secret).
"""

import stat
import tempfile
from pathlib import Path

from snektest import assert_eq, test

from danube.cli import _run_init, build_parser, render_config_template
from danube.security.crypto import KEY_SIZE


def _init_args(directory: Path, *, force: bool = False):  # noqa: ANN202 - argparse.Namespace
    parser = build_parser()
    argv = [
        "init",
        "--data-dir",
        str(directory / "lib"),
        "--config",
        str(directory / "etc" / "danube.toml"),
    ]
    if force:
        argv.append("--force")
    return parser.parse_args(argv)


@test(mark="fast")
def test_render_config_template_includes_sections() -> None:
    rendered = render_config_template(Path("/var/lib/danube"))
    assert "[server]" in rendered
    assert 'data_dir = "/var/lib/danube"' in rendered
    assert "[database]" in rendered
    assert "[auth]" in rendered


@test(mark="fast")
def test_parser_init_defaults() -> None:
    parser = build_parser()
    args = parser.parse_args(["init"])
    assert_eq(str(args.data_dir), "/var/lib/danube")
    assert_eq(str(args.config), "/etc/danube/danube.toml")
    assert_eq(args.force, False)
    assert args.handler is not None


@test(mark="medium")
def test_init_creates_tree_key_and_config() -> None:
    with tempfile.TemporaryDirectory() as raw_dir:
        directory = Path(raw_dir)
        args = _init_args(directory)

        code = _run_init(args)

        assert_eq(code, 0)
        data_dir = directory / "lib"
        for sub in ("keys", "logs", "artifacts", "workspaces"):
            assert (data_dir / sub).is_dir()
        key_path = data_dir / "keys" / "encryption.key"
        assert_eq(len(key_path.read_bytes()), KEY_SIZE)
        assert_eq(stat.S_IMODE(key_path.stat().st_mode), 0o600)
        config_path = directory / "etc" / "danube.toml"
        assert "[server]" in config_path.read_text(encoding="utf-8")


@test(mark="medium")
def test_init_is_idempotent_and_preserves_key() -> None:
    with tempfile.TemporaryDirectory() as raw_dir:
        directory = Path(raw_dir)

        assert_eq(_run_init(_init_args(directory)), 0)
        key_path = directory / "lib" / "keys" / "encryption.key"
        original_key = key_path.read_bytes()

        # Second run without --force must not regenerate the key.
        assert_eq(_run_init(_init_args(directory)), 0)
        assert_eq(key_path.read_bytes(), original_key)


@test(mark="medium")
def test_init_force_regenerates_key() -> None:
    with tempfile.TemporaryDirectory() as raw_dir:
        directory = Path(raw_dir)

        assert_eq(_run_init(_init_args(directory)), 0)
        key_path = directory / "lib" / "keys" / "encryption.key"
        original_key = key_path.read_bytes()

        assert_eq(_run_init(_init_args(directory, force=True)), 0)
        assert key_path.read_bytes() != original_key
