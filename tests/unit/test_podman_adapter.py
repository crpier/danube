"""Unit tests for the Podman adapter's pure translation logic.

These cover the parts of `PodmanAdapter` that do not need a live socket: the
exec-stream demultiplexer and the `ContainerSpec` -> libpod SpecGenerator body
translation (where the security defaults are encoded). The HTTP round-trips
themselves are exercised by the socket-guarded integration tests.
"""

from snektest import assert_eq, test

from danube.runner.podman import Mount, ResourceLimits
from danube.runner.podman.adapter import (
    ContainerSpec,
    _container_body,
    demultiplex_stream,
)


def _frame(stream_type: int, payload: bytes) -> bytes:
    header = bytes([stream_type, 0, 0, 0]) + len(payload).to_bytes(4, "big")
    return header + payload


@test(mark="fast")
def test_demultiplex_splits_stdout_and_stderr() -> None:
    data = _frame(1, b"out-a") + _frame(2, b"err-1") + _frame(1, b"out-b")

    output = demultiplex_stream(data)

    assert_eq(output.stdout, "out-aout-b")
    assert_eq(output.stderr, "err-1")


@test(mark="fast")
def test_demultiplex_ignores_truncated_trailing_frame() -> None:
    data = _frame(1, b"ok") + b"\x01\x00\x00\x00\x00\x00\x00"  # 7-byte stub header

    output = demultiplex_stream(data)

    assert_eq(output.stdout, "ok")
    assert_eq(output.stderr, "")


@test(mark="fast")
def test_demultiplex_empty_stream() -> None:
    output = demultiplex_stream(b"")
    assert_eq(output.stdout, "")
    assert_eq(output.stderr, "")


@test(mark="fast")
def test_container_body_encodes_security_defaults() -> None:
    spec = ContainerSpec(
        name="danube-job-j1-worker",
        image="busybox:latest",
        pod="danube-job-j1",
        labels={"io.danube.managed": "true"},
        command=("sleep", "infinity"),
        env={"FOO": "bar"},
        mounts=(Mount(source="/data/ws/j1", destination="/workspace"),),
        work_dir="/workspace",
        read_only_rootfs=True,
        no_new_privileges=True,
        cap_drop=("ALL",),
        limits=ResourceLimits(
            cpu_quota=200_000, cpu_period=100_000, memory_bytes=2048, pids_limit=64
        ),
    )

    body = _container_body(spec)

    assert_eq(body["privileged"], False)
    assert_eq(body["read_only_filesystem"], True)
    assert_eq(body["cap_drop"], ["ALL"])
    assert_eq(body["cap_add"], [])
    assert_eq(body["security_opt"], ["no-new-privileges"])
    assert_eq(body["pod"], "danube-job-j1")
    assert_eq(body["env"], {"FOO": "bar"})
    assert_eq(
        body["mounts"],
        [
            {
                "type": "bind",
                "source": "/data/ws/j1",
                "destination": "/workspace",
                "options": ["rw"],
            }
        ],
    )
    assert_eq(
        body["resource_limits"],
        {
            "cpu": {"quota": 200_000, "period": 100_000},
            "memory": {"limit": 2048},
            "pids": {"limit": 64},
        },
    )


@test(mark="fast")
def test_container_body_omits_no_new_privileges_when_disabled() -> None:
    spec = ContainerSpec(
        name="c",
        image="busybox",
        pod="p",
        labels={},
        no_new_privileges=False,
    )

    body = _container_body(spec)

    assert "security_opt" not in body
    assert "resource_limits" not in body
