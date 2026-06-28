"""Unit tests for `AuthConfig.from_env`.

Focus on the opt-in toggle and the parsing of the clock-skew leeway knob, which
must fail loud on bad input rather than silently zeroing a security parameter.
"""

import os
from collections.abc import Generator
from contextlib import contextmanager

from snektest import assert_eq, assert_raises, test

from danube.auth.config import (
    AUTH_AUDIENCE_ENV,
    AUTH_ENABLED_ENV,
    AUTH_HS256_SECRET_ENV,
    AUTH_ISSUER_ENV,
    AUTH_LEEWAY_SECONDS_ENV,
    AuthConfig,
    AuthConfigError,
)

_MANAGED_ENV = (
    AUTH_ENABLED_ENV,
    AUTH_ISSUER_ENV,
    AUTH_AUDIENCE_ENV,
    AUTH_HS256_SECRET_ENV,
    AUTH_LEEWAY_SECONDS_ENV,
)


@contextmanager
def _clean_env() -> Generator[None]:
    """Snapshot and restore the auth env vars around a test body."""
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


def _enable_hs256() -> None:
    os.environ[AUTH_ENABLED_ENV] = "true"
    os.environ[AUTH_ISSUER_ENV] = "https://idp.test"
    os.environ[AUTH_AUDIENCE_ENV] = "danube"
    os.environ[AUTH_HS256_SECRET_ENV] = "super-secret-signing-key"


@test(mark="fast")
def test_from_env_disabled_returns_none() -> None:
    with _clean_env():
        assert_eq(AuthConfig.from_env(), None)


@test(mark="fast")
def test_from_env_parses_valid_leeway() -> None:
    with _clean_env():
        _enable_hs256()
        os.environ[AUTH_LEEWAY_SECONDS_ENV] = "30"

        config = AuthConfig.from_env()

    assert config is not None
    assert_eq(config.leeway_seconds, 30)


@test(mark="fast")
def test_from_env_rejects_non_integer_leeway() -> None:
    with _clean_env():
        _enable_hs256()
        os.environ[AUTH_LEEWAY_SECONDS_ENV] = "not-a-number"

        with assert_raises(AuthConfigError):
            _ = AuthConfig.from_env()


@test(mark="fast")
def test_from_env_rejects_negative_leeway() -> None:
    with _clean_env():
        _enable_hs256()
        os.environ[AUTH_LEEWAY_SECONDS_ENV] = "-5"

        with assert_raises(AuthConfigError):
            _ = AuthConfig.from_env()
