"""Tests for the `danube.context` SDK surface (`JobContext`).

`JobContext` exposes the run metadata the Master plumbs into the Coordinator's
environment: the job id, pipeline name, trigger type, and the raw `branch/sha`
``trigger_ref`` (decomposed into `branch`/`sha`). A manual run carries no Git
ref, so those decompose to `None` cleanly.
"""

from snektest import assert_eq, test

from danube.sdk import JobContext
from danube.sdk.client import (
    ENV_JOB_ID,
    ENV_PIPELINE,
    ENV_TRIGGER_REF,
    ENV_TRIGGER_TYPE,
)


@test(mark="fast")
def test_from_mapping_reads_full_context() -> None:
    ctx = JobContext.from_mapping(
        {
            ENV_JOB_ID: "job-1",
            ENV_PIPELINE: "demo",
            ENV_TRIGGER_TYPE: "webhook",
            ENV_TRIGGER_REF: "main/abc123",
        }
    )

    assert_eq(ctx.job_id, "job-1")
    assert_eq(ctx.pipeline, "demo")
    assert_eq(ctx.trigger_type, "webhook")
    assert_eq(ctx.trigger_ref, "main/abc123")
    assert_eq(ctx.branch, "main")
    assert_eq(ctx.sha, "abc123")


@test(mark="fast")
def test_branch_with_slashes_splits_on_last_segment() -> None:
    ctx = JobContext.from_mapping(
        {
            ENV_JOB_ID: "job-1",
            ENV_PIPELINE: "demo",
            ENV_TRIGGER_TYPE: "webhook",
            ENV_TRIGGER_REF: "feature/nested/name/deadbeef",
        }
    )

    assert_eq(ctx.branch, "feature/nested/name")
    assert_eq(ctx.sha, "deadbeef")


@test(mark="fast")
def test_manual_job_has_no_ref() -> None:
    ctx = JobContext.from_mapping(
        {
            ENV_JOB_ID: "job-1",
            ENV_PIPELINE: "demo",
            ENV_TRIGGER_TYPE: "manual",
        }
    )

    assert_eq(ctx.trigger_ref, None)
    assert_eq(ctx.branch, None)
    assert_eq(ctx.sha, None)
