"""Observability: structured logging, Prometheus metrics, tracing, readiness.

`docs/architecture/observability.md` is the contract. This package provides the
in-house, dependency-free building blocks the Master wires together:

- `configure_logging` / `JsonLogFormatter` — structured JSON logs to stdout.
- `Metrics` — the documented Prometheus metric surface over a `Registry`.
- `Tracer` — OpenTelemetry-shaped spans, gated by config, no-op when disabled.
- `run_checks` — the `/health/ready` subsystem probes.
- `ObservabilityConfig` — the `spec.observability` toggles.
"""

from danube.observability.config import ObservabilityConfig
from danube.observability.logging import (
    JsonLogFormatter,
    configure_logging,
    job_log_fields,
)
from danube.observability.metrics import (
    CONTENT_TYPE,
    Counter,
    Gauge,
    Histogram,
    Metrics,
    Registry,
)
from danube.observability.readiness import run_checks
from danube.observability.tracing import Span, Tracer

__all__ = [
    "CONTENT_TYPE",
    "Counter",
    "Gauge",
    "Histogram",
    "JsonLogFormatter",
    "Metrics",
    "ObservabilityConfig",
    "Registry",
    "Span",
    "Tracer",
    "configure_logging",
    "job_log_fields",
    "run_checks",
]
