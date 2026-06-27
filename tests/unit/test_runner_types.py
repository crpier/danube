"""Tests for the frozen runner DTOs in `danube.domain.runner_types`."""

from pydantic import BaseModel, ValidationError
from snektest import Param, assert_eq, assert_raises, test

from danube.domain.runner_types import (
    ExecResult,
    ExecStepRequest,
    JobHandle,
    ReconcileReport,
    RunnerHealth,
    StartJobRequest,
)

ALL_MODELS = [
    StartJobRequest,
    JobHandle,
    ExecStepRequest,
    ExecResult,
    ReconcileReport,
    RunnerHealth,
]

# One fully-valid keyword set per model, used to probe immutability generically.
VALID_KWARGS: dict[type[BaseModel], dict[str, object]] = {
    StartJobRequest: {
        "job_id": "j1",
        "pipeline_id": "p1",
        "worker_image": "img:latest",
    },
    JobHandle: {"job_id": "j1", "pod_id": "pod-1", "workspace_path": "/ws/j1"},
    ExecStepRequest: {"command": "echo hi"},
    ExecResult: {"exit_code": 0, "stdout": "", "stderr": ""},
    ReconcileReport: {},
    RunnerHealth: {"healthy": True, "runtime": "podman"},
}


@test(mark="fast")
def test_start_job_request_valid() -> None:
    req = StartJobRequest(
        job_id="j1",
        pipeline_id="p1",
        worker_image="img:latest",
        env={"K": "V"},
        max_duration_seconds=3600,
    )
    assert_eq(req.worker_image, "img:latest")
    assert_eq(req.env, {"K": "V"})


@test(mark="fast")
def test_start_job_request_defaults() -> None:
    req = StartJobRequest(job_id="j1", pipeline_id="p1", worker_image="img")
    assert_eq(req.env, {})
    assert_eq(req.max_duration_seconds, None)


@test(mark="fast")
def test_job_handle_valid() -> None:
    handle = JobHandle(job_id="j1", pod_id="pod-1", workspace_path="/ws/j1")
    assert_eq(handle.pod_id, "pod-1")


@test(mark="fast")
def test_exec_step_request_valid() -> None:
    req = ExecStepRequest(command="npm install", env={"CI": "1"}, timeout_seconds=60)
    assert_eq(req.command, "npm install")
    assert_eq(req.timeout_seconds, 60)


@test(mark="fast")
def test_exec_result_valid() -> None:
    result = ExecResult(exit_code=1, stdout="out", stderr="err")
    assert_eq(result.exit_code, 1)
    assert_eq(result.stderr, "err")


@test(mark="fast")
def test_reconcile_report_defaults_empty() -> None:
    report = ReconcileReport()
    assert_eq(report.stale_pods, [])
    assert_eq(report.missing_pods, [])


@test(mark="fast")
def test_runner_health_valid() -> None:
    health = RunnerHealth(healthy=False, runtime="podman", detail="socket down")
    assert_eq(health.healthy, False)
    assert_eq(health.detail, "socket down")


@test(
    [Param(value=model, name=model.__name__) for model in ALL_MODELS],
    mark="fast",
)
def test_models_are_frozen(value: type[BaseModel]) -> None:
    instance = value(**VALID_KWARGS[value])
    field = next(iter(value.model_fields))
    with assert_raises(ValidationError):
        setattr(instance, field, getattr(instance, field))


@test(
    [Param(value=model, name=model.__name__) for model in ALL_MODELS],
    mark="fast",
)
def test_models_forbid_extra_fields(value: type[BaseModel]) -> None:
    with assert_raises(ValidationError):
        value(**VALID_KWARGS[value], unexpected="x")


@test(mark="fast")
def test_missing_required_field_raises() -> None:
    with assert_raises(ValidationError):
        StartJobRequest(job_id="j1", pipeline_id="p1")  # type: ignore[call-arg]


@test(mark="fast")
def test_wrong_type_raises() -> None:
    with assert_raises(ValidationError):
        ExecResult(exit_code="zero", stdout="", stderr="")  # type: ignore[arg-type]


@test(mark="fast")
def test_empty_required_string_raises() -> None:
    with assert_raises(ValidationError):
        JobHandle(job_id="", pod_id="pod-1", workspace_path="/ws")


@test(mark="fast")
def test_non_positive_timeout_raises() -> None:
    with assert_raises(ValidationError):
        ExecStepRequest(command="echo hi", timeout_seconds=0)
