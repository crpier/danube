"""Unit tests for the in-house Prometheus metrics registry.

Exercise the exposition format directly: counters/gauges/histograms render valid,
parseable Prometheus text, labels are escaped, and the `Metrics` surface exposes
the documented series.
"""

from snektest import assert_eq, assert_raises, test

from danube.observability.metrics import (
    Counter,
    Gauge,
    MetricError,
    Metrics,
    Registry,
)


def _series(text: str) -> dict[str, float]:
    """Parse a Prometheus exposition body into a {series: value} mapping."""
    out: dict[str, float] = {}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        name, _, value = line.rpartition(" ")
        out[name] = float(value)
    return out


@test(mark="fast")
def test_unlabelled_counter_starts_at_zero_and_increments() -> None:
    registry = Registry()
    counter = registry.counter("danube_things_total", "things")

    assert_eq(_series(registry.render())["danube_things_total"], 0.0)
    counter.inc()
    counter.inc(2.0)
    assert_eq(_series(registry.render())["danube_things_total"], 3.0)


@test(mark="fast")
def test_counter_rejects_negative_and_unknown_labels() -> None:
    counter = Counter("c", "c", ("status",))
    with assert_raises(MetricError):
        counter.inc(status="ok", extra="no")
    with assert_raises(MetricError):
        counter.inc(-1.0, status="ok")


@test(mark="fast")
def test_labelled_counter_renders_one_series_per_label_set() -> None:
    registry = Registry()
    counter = registry.counter("danube_jobs_total", "jobs", ("status",))
    counter.inc(status="success")
    counter.inc(status="success")
    counter.inc(status="failure")

    series = _series(registry.render())
    assert_eq(series['danube_jobs_total{status="success"}'], 2.0)
    assert_eq(series['danube_jobs_total{status="failure"}'], 1.0)


@test(mark="fast")
def test_gauge_inc_dec_set() -> None:
    gauge = Gauge("g", "g")
    gauge.inc()
    gauge.inc()
    gauge.dec()
    assert_eq(gauge.value(), 1.0)
    gauge.set(5.0)
    assert_eq(gauge.value(), 5.0)


@test(mark="fast")
def test_histogram_renders_cumulative_buckets_sum_and_count() -> None:
    registry = Registry()
    histogram = registry.histogram("danube_dur_seconds", "dur", buckets=(0.1, 1.0))
    histogram.observe(0.05)
    histogram.observe(0.5)
    histogram.observe(20.0)

    series = _series(registry.render())
    assert_eq(series['danube_dur_seconds_bucket{le="0.1"}'], 1.0)
    assert_eq(series['danube_dur_seconds_bucket{le="1"}'], 2.0)
    assert_eq(series['danube_dur_seconds_bucket{le="+Inf"}'], 3.0)
    assert_eq(series["danube_dur_seconds_count"], 3.0)
    assert_eq(series["danube_dur_seconds_sum"], 20.55)


@test(mark="fast")
def test_label_values_are_escaped() -> None:
    counter = Counter("c", "c", ("path",))
    counter.inc(path='a"b\\c')
    line = counter.render()[-1]
    assert r"a\"b\\c" in line


@test(mark="fast")
def test_metrics_surface_exposes_documented_series() -> None:
    text = Metrics().render()
    for name in (
        "danube_jobs_total",
        "danube_active_jobs",
        "danube_job_duration_seconds",
        "danube_runner_operations_total",
        "danube_runner_containers_active",
        "danube_db_queries_total",
    ):
        assert f"# TYPE {name} " in text
