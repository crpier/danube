"""Helpers for resolving config values across the documented loading order.

Danube resolves each setting as defaults -> `danube.toml` -> environment (see
`docs/configuration/server-config.md`). These helpers apply that precedence for a
single value: an environment variable wins when set, otherwise the value from the
parsed TOML sub-table is used, otherwise the hardcoded default.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

_TRUTHY = frozenset({"1", "true", "yes", "on"})


def str_source(
    env_name: str,
    table: Mapping[str, Any],
    key: str,
    *,
    default: str | None,
) -> str | None:
    """Resolve a string setting: env var, then `table[key]`, then `default`."""
    raw = os.environ.get(env_name)
    if raw is not None:
        return raw
    if key in table:
        return str(table[key])
    return default


def flag_source(
    env_name: str,
    table: Mapping[str, Any],
    key: str,
    *,
    default: bool,
) -> bool:
    """Resolve a boolean setting: env var, then `table[key]`, then `default`."""
    raw = os.environ.get(env_name)
    if raw is not None:
        return raw.strip().lower() in _TRUTHY
    if key in table:
        return bool(table[key])
    return default
