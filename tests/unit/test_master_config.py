"""Unit tests for `danube.master.load_config` TOML parsing and precedence.

Covers the documented loading order (defaults -> `danube.toml` -> environment)
for the fields the Master process actually consumes.
"""

import os
import tempfile
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from snektest import assert_eq, test

from danube.auth.config import (
    AUTH_AUDIENCE_ENV,
    AUTH_ENABLED_ENV,
    AUTH_HS256_SECRET_ENV,
    AUTH_ISSUER_ENV,
    AUTH_LEEWAY_SECONDS_ENV,
)
from danube.domain.limits import DEFAULT_CEILING
from danube.master import (
    BIND_ADDRESS_ENV,
    DATA_DIR_ENV,
    DEFAULT_BIND_ADDRESS,
    DEFAULT_DATA_DIR,
    DEFAULT_DATABASE_PATH,
    load_config,
)
from danube.observability.config import (
    METRICS_ENABLED_ENV,
    OTEL_ENDPOINT_ENV,
    TRACES_ENABLED_ENV,
)
from danube.observability.logging import LOG_LEVEL_ENV

_MANAGED_ENV = (
    BIND_ADDRESS_ENV,
    DATA_DIR_ENV,
    LOG_LEVEL_ENV,
    METRICS_ENABLED_ENV,
    TRACES_ENABLED_ENV,
    OTEL_ENDPOINT_ENV,
    AUTH_ENABLED_ENV,
    AUTH_ISSUER_ENV,
    AUTH_AUDIENCE_ENV,
    AUTH_HS256_SECRET_ENV,
    AUTH_LEEWAY_SECONDS_ENV,
)


@contextmanager
def _clean_env() -> Generator[None]:
    """Snapshot and restore the managed env vars around a test body."""
    saved = {name: os.environ.get(name) for name in _MANAGED_ENV}
    for name in _MANAGED_ENV:
        os.environ.pop(name, None)
    try:
        yield None
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


@contextmanager
def _toml_file(body: str) -> Generator[Path]:
    with tempfile.TemporaryDirectory() as raw_dir:
        path = Path(raw_dir) / "danube.toml"
        _ = path.write_text(body, encoding="utf-8")
        yield path


@test(mark="fast")
def test_load_config_defaults_without_file() -> None:
    with _clean_env():
        config = load_config(None)
    assert_eq(config.config_path, None)
    assert_eq(config.bind_address, DEFAULT_BIND_ADDRESS)
    assert_eq(str(config.database_path), DEFAULT_DATABASE_PATH)
    assert_eq(str(config.data_dir), DEFAULT_DATA_DIR)
    assert_eq(config.log_level, "INFO")
    assert_eq(config.auth, None)
    # No `[limits]` section: the built-in Resource Ceiling applies.
    assert_eq(config.limits, DEFAULT_CEILING)


@test(mark="fast")
def test_load_config_parses_limits_section() -> None:
    body = """
[limits.default]
cpu = 1.0
memory_mb = 1024

[limits.max]
cpu = 8.0
memory_mb = 16384
pids = 4096
timeout_seconds = 7200
"""
    with _clean_env(), _toml_file(body) as path:
        config = load_config(path)

    assert_eq(config.limits.default.cpu, 1.0)
    assert_eq(config.limits.default.memory_mb, 1024)
    # Unset default fields fall back to the built-in ceiling.
    assert_eq(config.limits.default.pids, DEFAULT_CEILING.default.pids)
    assert_eq(config.limits.max.cpu, 8.0)
    assert_eq(config.limits.max.memory_mb, 16384)
    assert_eq(config.limits.max.pids, 4096)
    assert_eq(config.limits.max.timeout_seconds, 7200)


@test(mark="fast")
def test_load_config_parses_toml_file() -> None:
    body = """
[server]
bind_address = "0.0.0.0:8080"
data_dir = "/srv/danube"

[database]
path = "/srv/danube/danube.db"

[logging]
level = "debug"

[observability]
metrics_enabled = false
traces_enabled = true
otel_endpoint = "http://collector:4317"

[auth]
enabled = true
issuer = "https://idp.example.com"
audience = "danube"
algorithm = "HS256"
hs256_secret = "shared-signing-secret"
leeway_seconds = 30
"""
    with _clean_env(), _toml_file(body) as path:
        config = load_config(path)

    assert_eq(config.config_path, path)
    assert_eq(config.bind_address, "0.0.0.0:8080")
    assert_eq(str(config.data_dir), "/srv/danube")
    assert_eq(str(config.database_path), "/srv/danube/danube.db")
    assert_eq(config.log_level, "debug")
    assert_eq(config.observability.metrics_enabled, False)
    assert_eq(config.observability.traces_enabled, True)
    assert_eq(config.observability.otel_endpoint, "http://collector:4317")
    assert config.auth is not None
    assert_eq(config.auth.issuer, "https://idp.example.com")
    assert_eq(config.auth.secret, "shared-signing-secret")
    assert_eq(config.auth.leeway_seconds, 30)


@test(mark="fast")
def test_env_overrides_file() -> None:
    body = """
[server]
bind_address = "0.0.0.0:8080"

[logging]
level = "warning"
"""
    with _clean_env(), _toml_file(body) as path:
        os.environ[BIND_ADDRESS_ENV] = "127.0.0.1:9999"
        os.environ[LOG_LEVEL_ENV] = "debug"
        config = load_config(path)

    assert_eq(config.bind_address, "127.0.0.1:9999")
    assert_eq(config.log_level, "debug")


@test(mark="fast")
def test_auth_disabled_in_file_returns_none() -> None:
    body = """
[auth]
enabled = false
issuer = "https://idp.example.com"
audience = "danube"
hs256_secret = "x"
"""
    with _clean_env(), _toml_file(body) as path:
        config = load_config(path)
    assert_eq(config.auth, None)
