"""Structured JSON logging for the Master process.

`configure_logging` installs a stdout handler that renders every record as a
single JSON object with the documented fields (`docs/architecture/observability.md`):
`timestamp`, `level`, `logger`, `event`, plus any contextual fields a call site
attaches via `extra=` (`job_id`, `pipeline`, `trigger_type`, `trigger_ref`, ...).

`event` is the record's message by default; pass `event` in `extra` to record a
stable machine token while keeping a human message out of band. The log level is
read from `DANUBE_LOG_LEVEL` (default `INFO`).

`job_log_fields` builds the contextual `extra` dict for a job from its row so the
orchestrator emits `job_started`/`job_finished` records with consistent keys.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from danube.db.models import Job

DEFAULT_LOG_LEVEL = "INFO"
LOG_LEVEL_ENV = "DANUBE_LOG_LEVEL"

# Standard `LogRecord` attributes; anything else on a record is a caller `extra`.
_RESERVED_ATTRS = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "module",
        "msecs",
        "message",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "taskName",
        "thread",
        "threadName",
    }
)

# Keys the formatter writes itself; a same-named `extra` must not clobber them.
_BASE_KEYS = frozenset({"timestamp", "level", "logger", "event"})


def _isoformat(created: float) -> str:
    """Render a `LogRecord.created` epoch as an ISO-8601 UTC string with `Z`."""
    moment = datetime.fromtimestamp(created, tz=UTC)
    return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")


class JsonLogFormatter(logging.Formatter):
    """Render log records as single-line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": _isoformat(record.created),
            "level": record.levelname.lower(),
            "logger": record.name,
            "event": record.__dict__.get("event", record.getMessage()),
        }
        for key, value in record.__dict__.items():
            if key in _RESERVED_ATTRS or key in _BASE_KEYS:
                continue
            payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: str | None = None) -> None:
    """Install the JSON stdout handler on the root logger.

    `level` overrides the `DANUBE_LOG_LEVEL` environment variable (default
    `INFO`). Replaces any existing handlers so logging is structured regardless of
    prior `basicConfig` calls.
    """
    resolved = (level or os.environ.get(LOG_LEVEL_ENV) or DEFAULT_LOG_LEVEL).upper()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonLogFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(resolved)


def job_log_fields(job: Job[Any]) -> dict[str, Any]:
    """Build the documented contextual log fields for a job row."""
    return {
        "job_id": job.id,
        "pipeline": job.pipeline_id,
        "trigger_type": job.trigger_type,
        "trigger_ref": job.trigger_ref,
    }
