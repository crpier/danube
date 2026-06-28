"""Lightweight, OpenTelemetry-shaped tracing for the Master.

`Tracer.span` is a context manager that models an OTel span: a name, attributes,
and a measured duration. It is a no-op when tracing is disabled, so call sites can
wrap HTTP handling and job execution unconditionally and pay nothing until traces
are turned on via config (`observability.traces_enabled`).

This is an in-house implementation rather than the OpenTelemetry SDK, for the same
reasons as the metrics registry: zero third-party type-stub friction under the
project's strict pyright config. When enabled, spans are emitted as DEBUG log
records (picked up by the structured JSON logger); an `otel_endpoint` is recorded
on the tracer as the future OTLP export hook. Crucially, enabling tracing with no
collector present never raises — nothing touches the network.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("danube.tracing")


@dataclass(slots=True)
class Span:
    """One in-flight span: a name plus mutable attributes."""

    name: str
    attributes: dict[str, Any] = field(default_factory=dict[str, Any])

    def set_attribute(self, key: str, value: Any) -> None:
        self.attributes[key] = value


class Tracer:
    """Create spans for traced operations; a no-op unless enabled."""

    def __init__(self, *, enabled: bool = False, endpoint: str | None = None) -> None:
        self.enabled = enabled
        self.endpoint = endpoint

    @contextmanager
    def span(self, name: str, **attributes: Any) -> Generator[Span]:
        """Open a span around a block of work.

        Yields a `Span` whose attributes can be augmented inside the block. When
        tracing is disabled this is a pure no-op (no timing, no logging).
        """
        current = Span(name, dict(attributes))
        if not self.enabled:
            yield current
            return
        start = time.monotonic()
        try:
            yield current
        finally:
            duration = time.monotonic() - start
            logger.debug(
                "span",
                extra={
                    "event": "span",
                    "span": current.name,
                    "duration_seconds": duration,
                    **current.attributes,
                },
            )
