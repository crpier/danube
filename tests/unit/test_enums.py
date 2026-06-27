"""Tests that domain enums stay string-valued and match the DB text used by the
persistence layer (`danube.db.models`)."""

from snektest import assert_eq, assert_isinstance, test

from danube.domain.enums import JobStatus, StepStatus, TriggerType


@test(mark="fast")
def test_job_status_values_match_db_text() -> None:
    assert_eq(
        {s.value for s in JobStatus},
        {
            "pending",
            "scheduling",
            "running",
            "success",
            "failure",
            "timeout",
            "cancelled",
        },
    )


@test(mark="fast")
def test_trigger_type_values_match_db_text() -> None:
    assert_eq({t.value for t in TriggerType}, {"webhook", "cron", "manual"})


@test(mark="fast")
def test_step_status_values_match_db_text() -> None:
    assert_eq(
        {s.value for s in StepStatus}, {"pending", "running", "success", "failure"}
    )


@test(mark="fast")
def test_members_are_str_instances() -> None:
    # StrEnum members compare equal to and serialize as their text value.
    assert_isinstance(JobStatus.PENDING, str)
    assert_eq(JobStatus.PENDING, "pending")
