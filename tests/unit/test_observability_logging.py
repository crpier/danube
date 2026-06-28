"""Unit tests for structured JSON logging."""

import json
import logging
import sys

from snektest import assert_eq, test

from danube.observability.logging import JsonLogFormatter


def _boom() -> None:
    msg = "boom"
    raise RuntimeError(msg)


def _record(message: str, **extra: object) -> logging.LogRecord:
    record = logging.LogRecord(
        name="danube.orchestrator",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=(),
        exc_info=None,
    )
    for key, value in extra.items():
        setattr(record, key, value)
    return record


@test(mark="fast")
def test_formatter_emits_required_fields_as_json() -> None:
    formatter = JsonLogFormatter()
    record = _record(
        "job_started",
        event="job_started",
        job_id="abc123",
        pipeline="frontend-build",
        trigger_type="webhook",
        trigger_ref="main/abc123def",
    )

    payload = json.loads(formatter.format(record))

    assert_eq(payload["level"], "info")
    assert_eq(payload["logger"], "danube.orchestrator")
    assert_eq(payload["event"], "job_started")
    assert_eq(payload["job_id"], "abc123")
    assert_eq(payload["pipeline"], "frontend-build")
    assert_eq(payload["trigger_type"], "webhook")
    assert_eq(payload["trigger_ref"], "main/abc123def")
    # The timestamp is ISO-8601 UTC with a trailing Z.
    assert payload["timestamp"].endswith("Z")


@test(mark="fast")
def test_event_defaults_to_message_when_not_provided() -> None:
    formatter = JsonLogFormatter()
    payload = json.loads(formatter.format(_record("plain message")))
    assert_eq(payload["event"], "plain message")


@test(mark="fast")
def test_exception_info_is_captured() -> None:
    formatter = JsonLogFormatter()
    record = _record("failed")
    try:
        _boom()
    except RuntimeError:
        record.exc_info = sys.exc_info()
    payload = json.loads(formatter.format(record))
    assert "RuntimeError" in payload["exception"]
