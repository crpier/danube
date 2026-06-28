"""Unit tests for the in-house tracer.

The tracer must be a true no-op when disabled and must not touch the network when
enabled with an OTLP endpoint but no collector present.
"""

from snektest import assert_eq, assert_raises, test

from danube.observability.tracing import Tracer


def _boom() -> None:
    msg = "boom"
    raise RuntimeError(msg)


@test(mark="fast")
def test_disabled_tracer_yields_usable_span() -> None:
    tracer = Tracer(enabled=False)
    with tracer.span("op", key="value") as span:
        span.set_attribute("extra", 1)
    assert_eq(span.attributes["key"], "value")
    assert_eq(span.attributes["extra"], 1)


@test(mark="fast")
def test_enabled_tracer_with_unreachable_endpoint_does_not_raise() -> None:
    # An endpoint is configured but no collector is listening; spanning must not
    # attempt any network I/O and must complete cleanly.
    tracer = Tracer(enabled=True, endpoint="http://127.0.0.1:4317")
    with tracer.span("job", job_id="j1") as span:
        span.set_attribute("status", "success")
    assert_eq(span.name, "job")


@test(mark="fast")
def test_span_propagates_exceptions() -> None:
    tracer = Tracer(enabled=True)
    with assert_raises(RuntimeError), tracer.span("op"):
        _boom()
