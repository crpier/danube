"""Observability configuration.

Mirrors the Blueprint `spec.observability` block
(`docs/configuration/blueprint-reference.md`): whether to expose/export metrics,
whether to emit traces, and the optional OTLP collector endpoint. `from_env`
builds a config from environment variables so the stubbed Master entrypoint can
toggle observability before full server-config parsing lands.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

METRICS_ENABLED_ENV = "DANUBE_METRICS_ENABLED"
TRACES_ENABLED_ENV = "DANUBE_TRACES_ENABLED"
OTEL_ENDPOINT_ENV = "DANUBE_OTEL_ENDPOINT"


def _env_flag(name: str, *, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class ObservabilityConfig:
    """Toggles for the metrics endpoint, OTLP export, and tracing."""

    metrics_enabled: bool = True
    traces_enabled: bool = False
    otel_endpoint: str | None = None

    @classmethod
    def from_env(cls) -> ObservabilityConfig:
        return cls(
            metrics_enabled=_env_flag(METRICS_ENABLED_ENV, default=True),
            traces_enabled=_env_flag(TRACES_ENABLED_ENV, default=False),
            otel_endpoint=os.environ.get(OTEL_ENDPOINT_ENV),
        )
