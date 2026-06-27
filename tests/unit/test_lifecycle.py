"""Tests for the job lifecycle state machine in `danube.domain.lifecycle`."""

from snektest import Param, assert_eq, assert_is, assert_raises, test

from danube.domain.enums import JobStatus
from danube.domain.lifecycle import InvalidTransition, transition

LEGAL_TRANSITIONS = [
    (JobStatus.PENDING, JobStatus.SCHEDULING),
    (JobStatus.SCHEDULING, JobStatus.RUNNING),
    (JobStatus.RUNNING, JobStatus.SUCCESS),
    (JobStatus.RUNNING, JobStatus.FAILURE),
    (JobStatus.RUNNING, JobStatus.TIMEOUT),
    (JobStatus.RUNNING, JobStatus.CANCELLED),
]

ILLEGAL_TRANSITIONS = [
    # Skipping intermediate states.
    (JobStatus.PENDING, JobStatus.RUNNING),
    (JobStatus.PENDING, JobStatus.SUCCESS),
    (JobStatus.SCHEDULING, JobStatus.SUCCESS),
    # Moving backwards.
    (JobStatus.SCHEDULING, JobStatus.PENDING),
    (JobStatus.RUNNING, JobStatus.SCHEDULING),
    # Self-transition.
    (JobStatus.RUNNING, JobStatus.RUNNING),
    # Leaving a terminal state.
    (JobStatus.SUCCESS, JobStatus.RUNNING),
    (JobStatus.FAILURE, JobStatus.PENDING),
    (JobStatus.TIMEOUT, JobStatus.SCHEDULING),
    (JobStatus.CANCELLED, JobStatus.RUNNING),
]


@test(
    [
        Param(value=pair, name=f"{c}_to_{t}")
        for pair in LEGAL_TRANSITIONS
        for c, t in [pair]
    ],
    mark="fast",
)
def test_legal_transition_returns_target(value: tuple[JobStatus, JobStatus]) -> None:
    current, target = value
    assert_eq(transition(current, target), target)


@test(
    [
        Param(value=pair, name=f"{c}_to_{t}")
        for pair in ILLEGAL_TRANSITIONS
        for c, t in [pair]
    ],
    mark="fast",
)
def test_illegal_transition_raises(value: tuple[JobStatus, JobStatus]) -> None:
    current, target = value
    with assert_raises(InvalidTransition) as ctx:
        transition(current, target)
    assert_is(ctx.exception.current, current)
    assert_is(ctx.exception.target, target)
