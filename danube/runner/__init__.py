"""Runner abstraction the Master depends on, plus test doubles.

The `Runner` protocol exposes Danube concepts (jobs, steps, workspaces) and hides
the execution backend (Podman, or the in-memory `FakeRunner`) behind it.
"""

from danube.runner.base import Runner
from danube.runner.fake import FakeRunner, JobNotActiveError, RecordedCall

__all__ = [
    "FakeRunner",
    "JobNotActiveError",
    "RecordedCall",
    "Runner",
]
