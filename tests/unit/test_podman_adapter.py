"""Unit tests for the Podman adapter's pure translation logic.

These cover the parts of `PodmanAdapter` that do not need a live socket: the
exec-stream demultiplexer and the `ContainerSpec` -> libpod SpecGenerator body
translation (where the security defaults are encoded). The HTTP round-trips
themselves are exercised by the socket-guarded integration tests.
"""

import base64
import io
import json
import tarfile
import tempfile
from pathlib import Path

import httpx
from snektest import assert_eq, test

from danube.runner.podman import Mount, ResourceLimits, Tmpfs
from danube.runner.podman.adapter import (
    BuildSpec,
    ContainerSpec,
    PodmanAdapter,
    PushSpec,
    RegistryAuth,
    _container_body,
    _registry_auth_header,
    demultiplex_stream,
    parse_build_stream,
    parse_push_stream,
    tar_build_context,
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
    # PID and IPC namespaces are explicitly private, never the host's.
    assert_eq(body["pidns"], {"nsmode": "private"})
    assert_eq(body["ipcns"], {"nsmode": "private"})
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
def test_container_body_encodes_tmpfs_scratch() -> None:
    # A read-only rootfs needs explicit tmpfs scratch mounts; the body must carry
    # them as OCI mounts of type "tmpfs", layered after the bind mounts.
    spec = ContainerSpec(
        name="danube-job-j1-worker",
        image="busybox:latest",
        pod="danube-job-j1",
        labels={"io.danube.managed": "true"},
        mounts=(Mount(source="/data/ws/j1", destination="/workspace"),),
        tmpfs=(
            Tmpfs(destination="/tmp"),
            Tmpfs(destination="/run", options=("rw", "noexec")),
        ),
        read_only_rootfs=True,
    )

    body = _container_body(spec)

    assert_eq(
        body["mounts"],
        [
            {
                "type": "bind",
                "source": "/data/ws/j1",
                "destination": "/workspace",
                "options": ["rw"],
            },
            {
                "type": "tmpfs",
                "source": "tmpfs",
                "destination": "/tmp",
                "options": ["rw", "nosuid", "nodev"],
            },
            {
                "type": "tmpfs",
                "source": "tmpfs",
                "destination": "/run",
                "options": ["rw", "noexec"],
            },
        ],
    )


@test(mark="fast")
def test_container_body_tmpfs_only_without_bind_mounts() -> None:
    # tmpfs scratch reaches the body even when there are no bind mounts.
    spec = ContainerSpec(
        name="danube-job-j1-worker",
        image="busybox:latest",
        pod="danube-job-j1",
        labels={"io.danube.managed": "true"},
        tmpfs=(Tmpfs(destination="/tmp"),),
    )

    body = _container_body(spec)

    assert_eq(
        body["mounts"],
        [
            {
                "type": "tmpfs",
                "source": "tmpfs",
                "destination": "/tmp",
                "options": ["rw", "nosuid", "nodev"],
            }
        ],
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


def _ndjson(*messages: dict[str, object]) -> bytes:
    return b"".join(json.dumps(message).encode() + b"\n" for message in messages)


@test(mark="fast")
def test_parse_build_stream_success_from_aux_id() -> None:
    content = _ndjson(
        {"stream": "STEP 1/2: FROM scratch\n"},
        {"stream": "STEP 2/2: COPY app /app\n"},
        {"aux": {"ID": "sha256:abc123"}},
        {"stream": "Successfully tagged localhost/app:latest\n"},
    )

    result = parse_build_stream(content)

    assert result.success is True
    assert_eq(result.image_id, "sha256:abc123")
    assert "STEP 1/2" in result.output


@test(mark="fast")
def test_parse_build_stream_success_from_built_line() -> None:
    # Some builders report the id only via a `Successfully built` stream line.
    content = _ndjson({"stream": "Successfully built deadbeef99\n"})

    result = parse_build_stream(content)

    assert result.success is True
    assert_eq(result.image_id, "deadbeef99")


@test(mark="fast")
def test_parse_build_stream_failure_records_error_and_no_id() -> None:
    content = _ndjson(
        {"stream": "STEP 1/1: RUN false\n"},
        {"error": "error building: exit status 1", "errorDetail": {"message": "boom"}},
    )

    result = parse_build_stream(content)

    assert result.success is False
    assert_eq(result.image_id, "")
    assert "error building" in result.output


@test(mark="fast")
def test_parse_build_stream_no_image_id_is_failure() -> None:
    # Output with neither an image id nor an error still cannot be called a success.
    result = parse_build_stream(_ndjson({"stream": "noise\n"}))

    assert result.success is False
    assert_eq(result.image_id, "")


@test(mark="fast")
def test_tar_build_context_packs_dockerfile_at_root() -> None:
    with tempfile.TemporaryDirectory() as raw:
        context = Path(raw)
        (context / "Dockerfile").write_text("FROM scratch\n")
        (context / "src").mkdir()
        (context / "src" / "app.py").write_text("print('hi')\n")

        archive = tar_build_context(context)

    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        names = set(tar.getnames())
    # The Dockerfile lands at the archive root (no context-dir prefix) so libpod's
    # default `dockerfile=Dockerfile` resolves it.
    assert "Dockerfile" in names
    assert "src/app.py" in names


async def _build_with(spec: BuildSpec) -> httpx.QueryParams:
    """Run `PodmanAdapter.build_image` against a mock transport and return the
    query params libpod received."""
    seen: list[httpx.QueryParams] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.params)
        return httpx.Response(200, content=_ndjson({"aux": {"ID": "sha256:ok"}}))

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://podman"
    ) as client:
        adapter = PodmanAdapter(client)
        result = await adapter.build_image(spec)
    assert result.success is True
    return seen[0]


@test(mark="fast")
async def test_build_image_denies_network_by_default() -> None:
    with tempfile.TemporaryDirectory() as raw:
        (Path(raw) / "Dockerfile").write_text("FROM scratch\n")
        params = await _build_with(BuildSpec(context_path=raw, tag="app:1"))

    # Egress denied by default per ADR-0001; no build args or target are sent.
    assert_eq(params.get("networkmode"), "none")
    assert "buildargs" not in params
    assert "target" not in params


@test(mark="fast")
async def test_build_image_sends_build_args_network_and_target() -> None:
    with tempfile.TemporaryDirectory() as raw:
        (Path(raw) / "Dockerfile").write_text("FROM scratch\n")
        params = await _build_with(
            BuildSpec(
                context_path=raw,
                tag="app:1",
                build_args={"VERSION": "1.2.3"},
                network=True,
                target="runtime",
            )
        )

    assert_eq(params.get("networkmode"), "default")
    assert_eq(json.loads(params["buildargs"]), {"VERSION": "1.2.3"})
    assert_eq(params.get("target"), "runtime")


@test(mark="fast")
def test_parse_push_stream_success_reads_explicit_digest() -> None:
    content = _ndjson(
        {"stream": "Getting image source signatures\n"},
        {"stream": "Copying blob sha256:abc\n"},
        {"stream": "Writing manifest to image destination\n"},
        {"digest": "sha256:" + "a" * 64},
    )

    result = parse_push_stream(content)

    assert result.success is True
    assert_eq(result.digest, "sha256:" + "a" * 64)
    assert "Writing manifest" in result.output


@test(mark="fast")
def test_parse_push_stream_reads_digest_from_progress_text() -> None:
    # Some libpod versions only surface the digest inside a progress line.
    digest = "sha256:" + "b" * 64
    content = _ndjson({"stream": f"Writing manifest to image destination\n{digest}\n"})

    result = parse_push_stream(content)

    assert result.success is True
    assert_eq(result.digest, digest)


@test(mark="fast")
def test_parse_push_stream_progress_digest_prefers_manifest_over_blob() -> None:
    # Without an explicit digest record, a blob digest precedes the manifest one in
    # the progress text; the manifest (written last) must win.
    blob = "sha256:" + "a" * 64
    manifest = "sha256:" + "b" * 64
    content = _ndjson(
        {"stream": f"Copying blob {blob}\n"},
        {"stream": f"Writing manifest to image destination\n{manifest}\n"},
    )

    result = parse_push_stream(content)

    assert result.success is True
    assert_eq(result.digest, manifest)


@test(mark="fast")
def test_parse_push_stream_failure_records_error() -> None:
    content = _ndjson(
        {"stream": "Getting image source signatures\n"},
        {"error": "unauthorized: authentication required"},
    )

    result = parse_push_stream(content)

    assert result.success is False
    assert_eq(result.digest, "")
    assert "unauthorized" in result.output


@test(mark="fast")
def test_registry_auth_header_is_base64url_json() -> None:
    header = _registry_auth_header(RegistryAuth(username="ci", password="pw"))
    decoded = json.loads(base64.urlsafe_b64decode(header))

    assert_eq(decoded, {"username": "ci", "password": "pw"})


async def _push_with(spec: PushSpec) -> tuple[httpx.QueryParams, httpx.Headers, str]:
    """Run `PodmanAdapter.push_image` against a mock transport and return the
    libpod request's query params, headers, and path."""
    seen: list[tuple[httpx.QueryParams, httpx.Headers, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.params, request.headers, request.url.path))
        return httpx.Response(200, content=_ndjson({"digest": "sha256:" + "c" * 64}))

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://podman"
    ) as client:
        adapter = PodmanAdapter(client)
        result = await adapter.push_image(spec)
    assert result.success is True
    return seen[0]


@test(mark="fast")
async def test_push_image_sends_destination_and_auth_header() -> None:
    params, headers, path = await _push_with(
        PushSpec(
            source="app:1",
            destination="registry.example.com/app:1",
            auth=RegistryAuth(username="ci", password="pw"),
        )
    )

    # The source reference is in the path; the destination and tls posture in params.
    assert path.endswith("/images/app:1/push")
    assert_eq(params.get("destination"), "registry.example.com/app:1")
    assert_eq(params.get("tlsVerify"), "true")
    # Credentials ride in the header, never the query string.
    assert "X-Registry-Auth" in headers
    assert "pw" not in str(params)


@test(mark="fast")
async def test_push_image_without_auth_omits_header_and_can_disable_tls() -> None:
    params, headers, _ = await _push_with(
        PushSpec(source="app:1", destination="localhost:5000/app:1", tls_verify=False)
    )

    assert "X-Registry-Auth" not in headers
    assert_eq(params.get("tlsVerify"), "false")


@test(mark="fast")
def test_container_body_encodes_host_namespace_escape_hatch() -> None:
    spec = ContainerSpec(
        name="c",
        image="busybox",
        pod="p",
        labels={},
        host_pid=True,
        host_ipc=True,
    )

    body = _container_body(spec)

    assert_eq(body["pidns"], {"nsmode": "host"})
    assert_eq(body["ipcns"], {"nsmode": "host"})
