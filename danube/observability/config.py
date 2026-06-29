"""Observability configuration.

Mirrors the Blueprint `spec.observability` block
(`docs/configuration/blueprint-reference.md`): whether to expose/export metrics,
whether to emit traces, and the optional OTLP collector endpoint. `from_env`
builds a config from environment variables so the stubbed Master entrypoint can
toggle observability before full server-config parsing lands.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from danube.configsource import flag_source, str_source

METRICS_ENABLED_ENV = "DANUBE_METRICS_ENABLED"
TRACES_ENABLED_ENV = "DANUBE_TRACES_ENABLED"
OTEL_ENDPOINT_ENV = "DANUBE_OTEL_ENDPOINT"


@dataclass(frozen=True, slots=True)
class ObservabilityConfig:
    """Toggles for the metrics endpoint, OTLP export, and tracing."""

    # Grouped by subsystem (metrics, then traces) rather than alphabetically: the
    # two enable flags read together, with the shared OTLP endpoint last.
    metrics_enabled: bool = True
    traces_enabled: bool = False
    otel_endpoint: str | None = None

    @classmethod
    def from_sources(
        cls, table: Mapping[str, Any] | None = None
    ) -> ObservabilityConfig:
        """Build from a `[observability]` table, with env vars overriding it."""
        table = table or {}
        return cls(
            metrics_enabled=flag_source(
                METRICS_ENABLED_ENV, table, "metrics_enabled", default=True
            ),
            traces_enabled=flag_source(
                TRACES_ENABLED_ENV, table, "traces_enabled", default=False
            ),
            otel_endpoint=str_source(
                OTEL_ENDPOINT_ENV, table, "otel_endpoint", default=None
            ),
        )

    @classmethod
    def from_env(cls) -> ObservabilityConfig:
        return cls.from_sources()
