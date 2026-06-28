"""Job lifecycle state machine.

Encodes the legal transitions from `docs/architecture/execution-model.md`:

    pending -> scheduling -> running -> {success | failure | timeout | cancelled}

`scheduling` may also fail directly: if the runner cannot create the job
environment (workspace/containers/network), the job moves straight to `failure`
without ever reaching `running`.

Terminal states (`success`, `failure`, `timeout`, `cancelled`) allow no further
transitions.
"""

from danube.domain.enums import JobStatus

ALLOWED_TRANSITIONS: dict[JobStatus, frozenset[JobStatus]] = {
    JobStatus.PENDING: frozenset({JobStatus.SCHEDULING}),
    JobStatus.SCHEDULING: frozenset({JobStatus.RUNNING, JobStatus.FAILURE}),
    JobStatus.RUNNING: frozenset(
        {
            JobStatus.SUCCESS,
            JobStatus.FAILURE,
            JobStatus.TIMEOUT,
            JobStatus.CANCELLED,
        }
    ),
    JobStatus.SUCCESS: frozenset(),
    JobStatus.FAILURE: frozenset(),
    JobStatus.TIMEOUT: frozenset(),
    JobStatus.CANCELLED: frozenset(),
}


# Issue #4 mandates the public name `InvalidTransition`; the N818 `Error` suffix
# convention is waived (below) to honour that named contract.
class InvalidTransition(Exception):  # noqa: N818
    """Raised when a job is moved between two states that the lifecycle forbids."""

    def __init__(self, current: JobStatus, target: JobStatus) -> None:
        self.current = current
        self.target = target
        super().__init__(f"illegal job transition: {current} -> {target}")


def transition(current: JobStatus, target: JobStatus) -> JobStatus:
    """Return `target` if moving from `current` to it is legal, else raise
    `InvalidTransition`."""
    if target not in ALLOWED_TRANSITIONS[current]:
        raise InvalidTransition(current, target)
    return target
