"""Master Core orchestration: drive jobs through their lifecycle.

`JobOrchestrator` persists jobs, asks a `Runner` to create each job's environment,
opens a control-plane session, and runs the Coordinator that drives the pipeline
over RPC — reusing `danube.domain.lifecycle` for all state transitions. Step
output is streamed to disk via `LogWriter` by the control plane.
"""

from danube.orchestrator.core import (
    CannotCancelError,
    JobNotFoundError,
    JobOrchestrator,
    PipelineNotFoundError,
)
from danube.orchestrator.log_writer import LogWriter, LogWriterError
from danube.orchestrator.manager import JobManager
from danube.orchestrator.scheduler import (
    DEFAULT_MAX_CONCURRENT_JOBS,
    Scheduler,
)

__all__ = [
    "DEFAULT_MAX_CONCURRENT_JOBS",
    "CannotCancelError",
    "JobManager",
    "JobNotFoundError",
    "JobOrchestrator",
    "LogWriter",
    "LogWriterError",
    "PipelineNotFoundError",
    "Scheduler",
]
