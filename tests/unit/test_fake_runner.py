"""Tests for the in-memory `FakeRunner` and its conformance to `Runner`."""

from collections.abc import Mapping
from typing import assert_type

from snektest import assert_eq, assert_raises, test

from danube.domain.runner_types import (
    BuildImageRequest,
    BuildImageResult,
    CoordinatorExit,
    ExecResult,
    ExecStepRequest,
    JobHandle,
    ReconcileReport,
    RunnerHealth,
    StartJobRequest,
)
from danube.runner import FakeRunner, JobNotActiveError, RecordedCall
from danube.runner.base import Runner

START = StartJobRequest(job_id="j1", pipeline_id="p1", worker_image="img:latest")


# A typed reference: assignable only if FakeRunner structurally satisfies Runner,
# so pyright fails the gate if the protocol and implementation drift apart.
_RUNNER: Runner = FakeRunner()


@test(mark="fast")
def test_fake_runner_satisfies_runner_protocol() -> None:
    runner = FakeRunner()
    assert_type(runner, FakeRunner)
    # Assignable only because FakeRunner structurally satisfies the protocol.
    typed: Runner = runner
    assert isinstance(typed, Runner)


@test(mark="fast")
async def test_full_job_call_sequence_and_results() -> None:
    runner = FakeRunner()
    runner.script_sequence(
        ExecResult(exit_code=0, stdout="installed", stderr=""),
        ExecResult(exit_code=2, stdout="", stderr="boom"),
    )

    handle = await runner.start_job(START)
    assert_eq(handle.job_id, "j1")
    assert_eq(handle.pod_id, "fake-pod-j1")
    assert_eq(handle.workspace_path, "/fake/workspaces/j1")

    install = ExecStepRequest(command="npm install")
    build = ExecStepRequest(command="npm run build")
    r1 = await runner.exec_step(handle, install)
    r2 = await runner.exec_step(handle, build)
    await runner.cleanup_job(handle)

    assert_eq(r1, ExecResult(exit_code=0, stdout="installed", stderr=""))
    assert_eq(r2, ExecResult(exit_code=2, stdout="", stderr="boom"))
    assert_eq(
        runner.method_names, ["start_job", "exec_step", "exec_step", "cleanup_job"]
    )
    assert_eq(
        runner.calls,
        [
            RecordedCall("start_job", (START,)),
            RecordedCall("exec_step", (handle, install)),
            RecordedCall("exec_step", (handle, build)),
            RecordedCall("cleanup_job", (handle,)),
        ],
    )


@test(mark="fast")
async def test_cleanup_is_idempotent_and_exec_after_cleanup_raises() -> None:
    runner = FakeRunner()
    handle = await runner.start_job(START)

    await runner.cleanup_job(handle)
    await runner.cleanup_job(handle)  # second call must not raise

    with assert_raises(JobNotActiveError) as ctx:
        await runner.exec_step(handle, ExecStepRequest(command="echo hi"))
    assert_eq(ctx.exception.job_id, "j1")
    # The failed exec is still recorded, after both cleanup calls.
    assert_eq(
        runner.method_names,
        ["start_job", "cleanup_job", "cleanup_job", "exec_step"],
    )


@test(mark="fast")
async def test_exec_against_unknown_job_raises() -> None:
    runner = FakeRunner()
    stray = JobHandle(job_id="ghost", pod_id="p", workspace_path="/ws")
    with assert_raises(JobNotActiveError):
        await runner.exec_step(stray, ExecStepRequest(command="ls"))


@test(mark="fast")
async def test_exec_after_stop_raises() -> None:
    runner = FakeRunner()
    handle = await runner.start_job(START)
    await runner.stop_job(handle, reason="timeout")
    with assert_raises(JobNotActiveError):
        await runner.exec_step(handle, ExecStepRequest(command="ls"))


@test(mark="fast")
async def test_per_command_script_takes_precedence_over_sequence() -> None:
    runner = FakeRunner()
    runner.script_sequence(ExecResult(exit_code=9, stdout="seq", stderr=""))
    runner.script_command(
        "flaky",
        ExecResult(exit_code=1, stdout="try1", stderr=""),
        ExecResult(exit_code=0, stdout="try2", stderr=""),
    )
    handle = await runner.start_job(START)

    first = await runner.exec_step(handle, ExecStepRequest(command="flaky"))
    second = await runner.exec_step(handle, ExecStepRequest(command="flaky"))
    fell_through = await runner.exec_step(handle, ExecStepRequest(command="other"))

    assert_eq(first.stdout, "try1")
    assert_eq(second.stdout, "try2")
    assert_eq(fell_through.stdout, "seq")


@test(mark="fast")
async def test_default_result_when_unscripted() -> None:
    runner = FakeRunner(default_result=ExecResult(exit_code=7, stdout="d", stderr=""))
    handle = await runner.start_job(START)
    result = await runner.exec_step(handle, ExecStepRequest(command="anything"))
    assert_eq(result, ExecResult(exit_code=7, stdout="d", stderr=""))


@test(mark="fast")
async def test_wait_for_coordinator_default_exit_zero() -> None:
    runner = FakeRunner()
    handle = await runner.start_job(START)

    await runner.start_coordinator(handle, {"DANUBE_JOB_ID": "j1"})
    exit_info = await runner.wait_for_coordinator(handle)

    assert_eq(exit_info, CoordinatorExit(exit_code=0))
    assert_eq(
        runner.method_names,
        ["start_job", "start_coordinator", "wait_for_coordinator"],
    )


@test(mark="fast")
async def test_coordinator_exit_code_is_configurable() -> None:
    runner = FakeRunner(coordinator_exit_code=3)
    handle = await runner.start_job(START)

    await runner.start_coordinator(handle, {})
    exit_info = await runner.wait_for_coordinator(handle)

    assert_eq(exit_info.exit_code, 3)


@test(mark="fast")
async def test_injected_coordinator_program_runs_with_env() -> None:
    seen: dict[str, str] = {}

    async def program(env: Mapping[str, str]) -> int:
        seen.update(env)
        return 0

    runner = FakeRunner(coordinator=program)
    handle = await runner.start_job(START)

    await runner.start_coordinator(handle, {"DANUBE_RPC_TOKEN": "tok"})
    exit_info = await runner.wait_for_coordinator(handle)

    assert_eq(exit_info.exit_code, 0)
    assert_eq(seen, {"DANUBE_RPC_TOKEN": "tok"})


@test(mark="fast")
async def test_crashing_coordinator_program_reports_nonzero_exit() -> None:
    async def program(_env: Mapping[str, str]) -> int:
        msg = "pipeline blew up"
        raise RuntimeError(msg)

    runner = FakeRunner(coordinator=program)
    handle = await runner.start_job(START)

    await runner.start_coordinator(handle, {})
    # A crashing Coordinator process exits non-zero; it is not a runner failure.
    exit_info = await runner.wait_for_coordinator(handle)

    assert exit_info.exit_code != 0
    assert "pipeline blew up" in exit_info.stderr


@test(mark="fast")
async def test_start_coordinator_against_inactive_job_raises() -> None:
    runner = FakeRunner()
    handle = await runner.start_job(START)
    await runner.cleanup_job(handle)

    with assert_raises(JobNotActiveError):
        await runner.start_coordinator(handle, {})


@test(mark="fast")
async def test_build_image_scripted_per_tag_and_recorded() -> None:
    runner = FakeRunner()
    runner.script_build(
        "app:abc",
        BuildImageResult(
            success=True, image_id="sha256:1", output="built\n", tag="app:abc"
        ),
    )
    handle = await runner.start_job(START)
    request = BuildImageRequest(tag="app:abc", context_path="/fake/workspaces/j1")

    result = await runner.build_image(handle, request)

    assert result.success is True
    assert_eq(result.image_id, "sha256:1")
    assert_eq(runner.method_names, ["start_job", "build_image"])
    assert_eq(runner.calls[-1], RecordedCall("build_image", (handle, request)))


@test(mark="fast")
async def test_build_image_default_result_when_unscripted() -> None:
    runner = FakeRunner()
    handle = await runner.start_job(START)

    result = await runner.build_image(
        handle, BuildImageRequest(tag="x:1", context_path="/ws")
    )

    assert result.success is True


@test(mark="fast")
async def test_build_image_against_inactive_job_raises() -> None:
    runner = FakeRunner()
    handle = await runner.start_job(START)
    await runner.cleanup_job(handle)

    with assert_raises(JobNotActiveError):
        await runner.build_image(
            handle, BuildImageRequest(tag="x:1", context_path="/ws")
        )


@test(mark="fast")
async def test_health_and_reconcile_are_configurable_and_recorded() -> None:
    health = RunnerHealth(healthy=False, runtime="fake", detail="down")
    report = ReconcileReport(stale_pods=["j9"])
    runner = FakeRunner(health_result=health, reconcile_result=report)

    assert_eq(await runner.health(), health)
    assert_eq(await runner.reconcile(), report)
    assert_eq(runner.method_names, ["health", "reconcile"])
