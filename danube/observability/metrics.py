"""In-house Prometheus metrics registry and the Danube metric surface.

This is a small, dependency-free implementation of the subset of the Prometheus
data model Danube needs: counters, gauges, and histograms with string labels,
rendered in the Prometheus text exposition format (version 0.0.4). It is used
instead of `prometheus_client` to stay consistent with the project's in-house,
strictly-typed tooling (snekql, snektest) and avoid third-party type-stub gaps.

A `Metrics` instance bundles the documented job/runner/database series
(`docs/architecture/observability.md`). It is injected — not a module global — so
the orchestrator records into the same registry the `/metrics` endpoint renders,
and tests get an isolated registry per case.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from threading import Lock

# Prometheus text exposition content type; pinned to the documented format version.
CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"

# Default histogram buckets (seconds), matching the Prometheus client defaults.
DEFAULT_BUCKETS: tuple[float, ...] = (
    0.005,
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
    math.inf,
)

_LabelValues = tuple[str, ...]


class MetricError(Exception):
    """Raised when a metric is used with labels that do not match its schema."""


def _escape_label_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


def _render_labels(names: Sequence[str], values: Sequence[str]) -> str:
    if not names:
        return ""
    pairs = [
        f'{name}="{_escape_label_value(value)}"'
        for name, value in zip(names, values, strict=True)
    ]
    return "{" + ",".join(pairs) + "}"


def _format_float(value: float) -> str:
    if value == math.inf:
        return "+Inf"
    if value == -math.inf:
        return "-Inf"
    if math.isnan(value):
        return "NaN"
    if value.is_integer():
        return str(int(value))
    return repr(value)


class _Metric:
    """Shared name/help/label bookkeeping for a single metric family."""

    def __init__(
        self, name: str, documentation: str, labelnames: Sequence[str] = ()
    ) -> None:
        self.name = name
        self.documentation = documentation
        self.labelnames: _LabelValues = tuple(labelnames)
        self._lock = Lock()

    def _key(self, labels: dict[str, str]) -> _LabelValues:
        if set(labels) != set(self.labelnames):
            msg = (
                f"metric {self.name!r} expects labels {self.labelnames}, "
                f"got {tuple(labels)}"
            )
            raise MetricError(msg)
        return tuple(labels[name] for name in self.labelnames)

    def _header(self, metric_type: str) -> list[str]:
        return [
            f"# HELP {self.name} {self.documentation}",
            f"# TYPE {self.name} {metric_type}",
        ]

    def render(self) -> list[str]:  # pragma: no cover - overridden
        raise NotImplementedError


class Counter(_Metric):
    """A monotonically increasing value, optionally partitioned by labels."""

    def __init__(
        self, name: str, documentation: str, labelnames: Sequence[str] = ()
    ) -> None:
        super().__init__(name, documentation, labelnames)
        self._values: dict[_LabelValues, float] = {}
        if not self.labelnames:
            # An unlabelled counter exposes a single series initialised to 0.
            self._values[()] = 0.0

    def inc(self, amount: float = 1.0, **labels: str) -> None:
        if amount < 0:
            msg = "counters can only increase"
            raise MetricError(msg)
        key = self._key(labels)
        with self._lock:
            self._values[key] = self._values.get(key, 0.0) + amount

    def value(self, **labels: str) -> float:
        key = self._key(labels)
        with self._lock:
            return self._values.get(key, 0.0)

    def render(self) -> list[str]:
        lines = self._header("counter")
        with self._lock:
            items = sorted(self._values.items())
        for key, value in items:
            labels = _render_labels(self.labelnames, key)
            lines.append(f"{self.name}{labels} {_format_float(value)}")
        return lines


class Gauge(_Metric):
    """A value that can go up or down (e.g. in-flight jobs)."""

    def __init__(
        self, name: str, documentation: str, labelnames: Sequence[str] = ()
    ) -> None:
        super().__init__(name, documentation, labelnames)
        self._values: dict[_LabelValues, float] = {}
        if not self.labelnames:
            self._values[()] = 0.0

    def inc(self, amount: float = 1.0, **labels: str) -> None:
        key = self._key(labels)
        with self._lock:
            self._values[key] = self._values.get(key, 0.0) + amount

    def dec(self, amount: float = 1.0, **labels: str) -> None:
        self.inc(-amount, **labels)

    def set(self, value: float, **labels: str) -> None:
        key = self._key(labels)
        with self._lock:
            self._values[key] = value

    def value(self, **labels: str) -> float:
        key = self._key(labels)
        with self._lock:
            return self._values.get(key, 0.0)

    def render(self) -> list[str]:
        lines = self._header("gauge")
        with self._lock:
            items = sorted(self._values.items())
        for key, value in items:
            labels = _render_labels(self.labelnames, key)
            lines.append(f"{self.name}{labels} {_format_float(value)}")
        return lines


class Histogram(_Metric):
    """Cumulative bucketed observations with a running sum and count."""

    def __init__(
        self,
        name: str,
        documentation: str,
        labelnames: Sequence[str] = (),
        buckets: Sequence[float] = DEFAULT_BUCKETS,
    ) -> None:
        super().__init__(name, documentation, labelnames)
        bounds = sorted(buckets)
        if not bounds or bounds[-1] != math.inf:
            bounds = [*bounds, math.inf]
        self._bounds: tuple[float, ...] = tuple(bounds)
        self._counts: dict[_LabelValues, list[float]] = {}
        self._sums: dict[_LabelValues, float] = {}

    def observe(self, value: float, **labels: str) -> None:
        key = self._key(labels)
        with self._lock:
            counts = self._counts.get(key)
            if counts is None:
                counts = [0.0] * len(self._bounds)
                self._counts[key] = counts
                self._sums[key] = 0.0
            self._sums[key] += value
            for index, bound in enumerate(self._bounds):
                if value <= bound:
                    counts[index] += 1.0

    def render(self) -> list[str]:
        lines = self._header("histogram")
        with self._lock:
            items = sorted(self._counts.items())
            sums = dict(self._sums)
        for key, counts in items:
            cumulative = 0.0
            for bound, count in zip(self._bounds, counts, strict=True):
                cumulative = count
                bucket_labels = _render_labels(
                    (*self.labelnames, "le"), (*key, _format_float(bound))
                )
                lines.append(
                    f"{self.name}_bucket{bucket_labels} {_format_float(cumulative)}"
                )
            base = _render_labels(self.labelnames, key)
            lines.append(f"{self.name}_sum{base} {_format_float(sums.get(key, 0.0))}")
            lines.append(f"{self.name}_count{base} {_format_float(counts[-1])}")
        return lines


class Registry:
    """Holds metrics in registration order and renders them as one document."""

    def __init__(self) -> None:
        self._metrics: list[_Metric] = []

    def counter(
        self, name: str, documentation: str, labelnames: Sequence[str] = ()
    ) -> Counter:
        metric = Counter(name, documentation, labelnames)
        self._metrics.append(metric)
        return metric

    def gauge(
        self, name: str, documentation: str, labelnames: Sequence[str] = ()
    ) -> Gauge:
        metric = Gauge(name, documentation, labelnames)
        self._metrics.append(metric)
        return metric

    def histogram(
        self,
        name: str,
        documentation: str,
        labelnames: Sequence[str] = (),
        buckets: Sequence[float] = DEFAULT_BUCKETS,
    ) -> Histogram:
        metric = Histogram(name, documentation, labelnames, buckets)
        self._metrics.append(metric)
        return metric

    def render(self) -> str:
        lines: list[str] = []
        for metric in self._metrics:
            lines.extend(metric.render())
        return "\n".join(lines) + "\n"


class Metrics:
    """The documented Danube metric surface, bound to a single `Registry`.

    Only the series with instrumentation points wired today are actively updated;
    the rest are exposed at their zero value so `/metrics` advertises the full
    documented surface and dashboards can be built before every component lands
    (`docs/architecture/observability.md`).
    """

    def __init__(self) -> None:
        self.registry = Registry()

        # Job metrics.
        self.jobs_total = self.registry.counter(
            "danube_jobs_total",
            "Total jobs that reached a terminal state, by status and pipeline.",
            ("status", "pipeline"),
        )
        self.job_duration_seconds = self.registry.histogram(
            "danube_job_duration_seconds",
            "Wall-clock duration of completed jobs, by pipeline.",
            ("pipeline",),
        )
        self.active_jobs = self.registry.gauge(
            "danube_active_jobs",
            "Jobs currently being driven by the orchestrator.",
        )
        self.job_queue_size = self.registry.gauge(
            "danube_job_queue_size",
            "Jobs waiting for a free concurrency slot.",
        )
        self.job_timeouts_total = self.registry.counter(
            "danube_job_timeouts_total",
            "Jobs that exceeded their max_duration_seconds, by pipeline.",
            ("pipeline",),
        )

        # Runner metrics.
        self.runner_operations_total = self.registry.counter(
            "danube_runner_operations_total",
            "Runner lifecycle operations, by operation and status.",
            ("operation", "status"),
        )
        self.runner_operation_duration_seconds = self.registry.histogram(
            "danube_runner_operation_duration_seconds",
            "Duration of runner lifecycle operations, by operation.",
            ("operation",),
        )
        self.runner_containers_active = self.registry.gauge(
            "danube_runner_containers_active",
            "Job environments the runner currently has live.",
        )
        self.runner_cleanup_failures_total = self.registry.counter(
            "danube_runner_cleanup_failures_total",
            "Runner cleanup operations that failed.",
        )
        self.runner_workspace_bytes = self.registry.gauge(
            "danube_runner_workspace_bytes",
            "Total bytes used by job workspaces.",
        )

        # Database metrics (exposed now; instrumentation lands with the DB layer).
        self.db_queries_total = self.registry.counter(
            "danube_db_queries_total",
            "Database queries executed, by operation.",
            ("operation",),
        )
        self.db_query_duration_seconds = self.registry.histogram(
            "danube_db_query_duration_seconds",
            "Database query duration, by operation.",
            ("operation",),
        )
        self.db_connections_active = self.registry.gauge(
            "danube_db_connections_active",
            "Active database connections.",
        )
        self.db_lock_wait_seconds_total = self.registry.counter(
            "danube_db_lock_wait_seconds_total",
            "Cumulative time spent waiting on database locks.",
        )

    def render(self) -> str:
        return self.registry.render()
