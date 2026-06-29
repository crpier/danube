"""Thin async adapter over the rootless Podman (libpod) HTTP API.

The adapter is the only place in Danube that speaks Podman. It talks to the
libpod REST API over a Unix domain socket with `httpx`, translating small Danube
spec dataclasses into libpod request bodies and libpod responses back into small
result dataclasses. Nothing above it (the `LocalContainerRunner`, the orchestrator)
should import Podman concepts; they depend on `PodmanAPI` instead.

Why a `Protocol` plus a concrete class: unit tests drive the runner with a fake
that satisfies `PodmanAPI` and records calls, while the real `PodmanAdapter` is
exercised by the socket-guarded integration tests. The wire-format translation is
intentionally small and covered by focused unit tests, because the integration
tests skip whenever no Podman socket is present.

References: `docs/architecture/local-runner.md` (Podman API), Podman libpod API.
"""

from __future__ import annotations

import base64
import io
import json
import os
import re
import tarfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, cast, runtime_checkable

import anyio.to_thread
import httpx

# libpod API version segment. Podman is backward compatible across minor
# versions, so pinning a base version the appliance ships with is enough.
DEFAULT_API_VERSION = "v5.0.0"

_HTTP_OK = 200
_HTTP_NO_CONTENT = 204


def default_socket_path() -> Path:
    """Best-effort path to the rootless Podman API socket for the current user.

    Honours `XDG_RUNTIME_DIR`; falls back to `/run/user/<uid>` which is where
    systemd places the per-user runtime directory on a typical rootless setup.
    """
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    base = Path(runtime_dir) if runtime_dir else Path(f"/run/user/{os.getuid()}")
    return base / "podman" / "podman.sock"


def socket_available(socket_path: Path | None = None) -> bool:
    """Whether a Podman API socket exists at `socket_path` (or the default).

    Used by integration tests to skip when no rootless Podman socket is present.
    Only checks for the socket file; it does not probe liveness.
    """
    path = socket_path or default_socket_path()
    return path.is_socket()


# --- Specs (Danube -> Podman request bodies) --------------------------------


@dataclass(frozen=True, slots=True)
class PodSpec:
    """A pod to create: one per Danube job, the job's network boundary."""

    name: str
    labels: Mapping[str, str]
    # Named Podman networks to attach. For default-deny egress these are
    # `internal` networks created via `ensure_network`.
    networks: Sequence[str] = ()


@dataclass(frozen=True, slots=True)
class Mount:
    """A bind mount from a host path into the container."""

    source: str
    destination: str
    read_only: bool = False


@dataclass(frozen=True, slots=True)
class ResourceLimits:
    """cgroup limits applied to a container. `None` leaves a limit unset."""

    cpu_quota: int | None = None
    cpu_period: int | None = None
    memory_bytes: int | None = None
    pids_limit: int | None = None


@dataclass(frozen=True, slots=True)
class ContainerSpec:
    """A container to create inside a pod, with security defaults applied."""

    name: str
    image: str
    pod: str
    labels: Mapping[str, str]
    command: Sequence[str] | None = None
    env: Mapping[str, str] = field(default_factory=dict[str, str])
    mounts: Sequence[Mount] = ()
    work_dir: str | None = None
    user: str | None = None
    privileged: bool = False
    # Namespace isolation. Defaults keep the container out of the host PID/IPC
    # namespaces; the network namespace is the pod's (an internal network), never
    # the host's. Set these True only for a deliberate, audited escape hatch.
    host_pid: bool = False
    host_ipc: bool = False
    read_only_rootfs: bool = False
    no_new_privileges: bool = True
    cap_drop: Sequence[str] = ("ALL",)
    cap_add: Sequence[str] = ()
    limits: ResourceLimits | None = None


@dataclass(frozen=True, slots=True)
class ExecSpec:
    """A command to exec inside an already-running container."""

    command: Sequence[str]
    env: Mapping[str, str] = field(default_factory=dict[str, str])
    work_dir: str | None = None
    user: str | None = None


@dataclass(frozen=True, slots=True)
class BuildSpec:
    """A host-side image build: a build context directory, a Containerfile path
    within it, and the tag to apply.

    `build_args` populate the Containerfile's `ARG` instructions (not secret-safe).
    `network` enables networking for `RUN` instructions; Danube defaults it off so
    build steps cannot reach the network (`docs/adr/0001-host-side-image-build.md`).
    `target` selects a stage to build in a multi-stage Containerfile."""

    context_path: str
    tag: str
    dockerfile: str = "Dockerfile"
    build_args: Mapping[str, str] = field(default_factory=dict[str, str])
    network: bool = False
    target: str | None = None


@dataclass(frozen=True, slots=True)
class RegistryAuth:
    """Username/password for a registry push, encoded into `X-Registry-Auth`."""

    username: str
    password: str


@dataclass(frozen=True, slots=True)
class PushSpec:
    """A host-side image push: a source reference in the Local Image Store, the
    destination reference to push it to, and optional registry auth.

    `tls_verify` defaults on; it is only disabled for an insecure (HTTP or
    self-signed) registry, e.g. a throwaway local registry."""

    source: str
    destination: str
    auth: RegistryAuth | None = None
    tls_verify: bool = True


# --- Results (Podman responses -> Danube dataclasses) -----------------------


@dataclass(frozen=True, slots=True)
class PodmanVersion:
    """Runtime version reported by the Podman service."""

    version: str
    api_version: str


@dataclass(frozen=True, slots=True)
class ResourceSummary:
    """A labelled Podman pod or container as seen by a list query."""

    id: str
    name: str
    labels: dict[str, str]


@dataclass(frozen=True, slots=True)
class ExecOutput:
    """Demultiplexed stdout/stderr captured from an exec session."""

    stdout: str
    stderr: str


@dataclass(frozen=True, slots=True)
class ExecInspect:
    """Terminal state of an exec session."""

    exit_code: int
    running: bool


@dataclass(frozen=True, slots=True)
class BuildResult:
    """Outcome of an image build parsed from the libpod build stream.

    `success` is true only when the stream carried a built image id and no error;
    `image_id` is that id (empty on failure); `output` is the human-readable build
    log (progress and any error message), suitable for the job log."""

    success: bool
    image_id: str
    output: str


@dataclass(frozen=True, slots=True)
class PushResult:
    """Outcome of an image push parsed from the libpod push stream.

    `success` is true when the stream carried no error; `digest` is the pushed
    manifest digest when one was reported (empty otherwise); `output` is the
    human-readable push log (progress and any error message)."""

    success: bool
    digest: str
    output: str


class PodmanError(Exception):
    """A libpod API call returned an unexpected status."""

    def __init__(self, method: str, path: str, status_code: int, body: str) -> None:
        self.method = method
        self.path = path
        self.status_code = status_code
        self.body = body
        super().__init__(f"{method} {path} -> {status_code}: {body}")


# --- The interface the runner depends on ------------------------------------


@runtime_checkable
class PodmanAPI(Protocol):
    """The Podman operations the `LocalContainerRunner` needs.

    Speaks Danube spec/result dataclasses only. Implemented for real by
    `PodmanAdapter` and by a recording fake in the runner's unit tests.
    """

    async def ping(self) -> bool: ...
    async def version(self) -> PodmanVersion: ...
    async def image_exists(self, reference: str) -> bool: ...
    async def pull_image(self, reference: str) -> None: ...
    async def ensure_network(
        self, name: str, *, internal: bool, labels: Mapping[str, str]
    ) -> None: ...
    async def create_pod(self, spec: PodSpec) -> str: ...
    async def start_pod(self, pod: str) -> None: ...
    async def stop_pod(self, pod: str, *, grace_seconds: int | None = None) -> None: ...
    async def remove_pod(self, pod: str, *, force: bool = True) -> None: ...
    async def list_pods(self, labels: Mapping[str, str]) -> list[ResourceSummary]: ...
    async def create_container(self, spec: ContainerSpec) -> str: ...
    async def remove_container(self, container: str, *, force: bool = True) -> None: ...
    async def list_containers(
        self, labels: Mapping[str, str]
    ) -> list[ResourceSummary]: ...
    async def exec_create(self, container: str, spec: ExecSpec) -> str: ...
    async def exec_start(self, exec_id: str) -> ExecOutput: ...
    async def exec_inspect(self, exec_id: str) -> ExecInspect: ...
    async def build_image(self, spec: BuildSpec) -> BuildResult: ...
    async def push_image(self, spec: PushSpec) -> PushResult: ...


# --- Stream demultiplexing --------------------------------------------------

_STREAM_STDERR = 2
_FRAME_HEADER_LEN = 8


def demultiplex_stream(data: bytes) -> ExecOutput:
    """Split a Docker/Podman multiplexed exec stream into stdout and stderr.

    The stream is a sequence of frames, each an 8-byte header
    `[stream_type, 0, 0, 0, size(4, big-endian)]` followed by `size` payload
    bytes. `stream_type` 2 is stderr; everything else (1 = stdout) is treated as
    stdout. A truncated trailing frame is ignored. Payloads are decoded as UTF-8
    with replacement so malformed bytes never raise.
    """
    stdout = bytearray()
    stderr = bytearray()
    offset = 0
    total = len(data)
    while offset + _FRAME_HEADER_LEN <= total:
        stream_type = data[offset]
        size = int.from_bytes(data[offset + 4 : offset + _FRAME_HEADER_LEN], "big")
        offset += _FRAME_HEADER_LEN
        chunk = data[offset : offset + size]
        offset += size
        if stream_type == _STREAM_STDERR:
            stderr += chunk
        else:
            stdout += chunk
    return ExecOutput(
        stdout=stdout.decode("utf-8", "replace"),
        stderr=stderr.decode("utf-8", "replace"),
    )


# --- Build context + stream parsing -----------------------------------------


def tar_build_context(context_path: str | Path) -> bytes:
    """Pack a build-context directory into an uncompressed tar archive.

    Members are stored relative to the context root (so a `Dockerfile` at the
    root lands at the archive root), which is the layout libpod's `/build`
    endpoint expects. Blocking IO: call it off the event loop."""
    root = Path(context_path)
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for child in sorted(root.iterdir()):
            archive.add(child, arcname=child.name)
    return buffer.getvalue()


def parse_build_stream(content: bytes) -> BuildResult:
    """Parse the libpod `/build` newline-delimited JSON stream into a `BuildResult`.

    Each line is a JSON object: `{"stream": "..."}` carries build output,
    `{"error": "..."}` (or `errorDetail`) signals failure, and `{"aux": {"ID": ...}}`
    or a trailing ``Successfully built <id>`` line carries the built image id. The
    build succeeds only if an image id was seen and no error occurred. Non-JSON or
    blank lines are tolerated so a partial trailing chunk never raises."""
    output = io.StringIO()
    image_id = ""
    errored = False
    for raw in content.splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            message: object = json.loads(line)
        except json.JSONDecodeError:
            _ = output.write(line.decode("utf-8", "replace"))
            continue
        if not isinstance(message, dict):
            continue
        record = cast("dict[str, Any]", message)
        stream = record.get("stream")
        if isinstance(stream, str):
            _ = output.write(stream)
            built = _built_image_id(stream)
            if built:
                image_id = built
        aux = record.get("aux")
        if isinstance(aux, dict):
            aux_id = cast("dict[str, Any]", aux).get("ID")
            if isinstance(aux_id, str) and aux_id:
                image_id = aux_id
        error: object = record.get("error") or record.get("errorDetail")
        if error:
            errored = True
            _ = output.write(_error_text(error))
    return BuildResult(
        success=bool(image_id) and not errored,
        image_id=image_id,
        output=output.getvalue(),
    )


_BUILT_PREFIX = "Successfully built "


def _built_image_id(stream: str) -> str:
    """Extract the image id from a ``Successfully built <id>`` progress line."""
    for piece in stream.splitlines():
        text = piece.strip()
        if text.startswith(_BUILT_PREFIX):
            return text.removeprefix(_BUILT_PREFIX).strip()
    return ""


def _error_text(error: object) -> str:
    if isinstance(error, str):
        return error
    if isinstance(error, dict):
        message = cast("dict[str, Any]", error).get("message")
        if isinstance(message, str):
            return message
    return ""


_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}")


def parse_push_stream(content: bytes) -> PushResult:
    """Parse the libpod `/images/{name}/push` newline-delimited JSON stream.

    Each line is a JSON object: `{"stream": "..."}` carries push progress and
    `{"error": "..."}` (or `errorDetail`) signals failure. The push succeeds when
    no error record was seen. The manifest `digest` comes from an explicit
    `{"digest": "..."}` record when present; otherwise it falls back to the *last*
    ``sha256:<64-hex>`` token in the progress text. Last, not first: a push emits a
    digest per blob before writing the manifest, so the manifest digest is the
    final one — taking the first would report a blob digest instead. Non-JSON or
    blank lines are tolerated so a partial trailing chunk never raises."""
    output = io.StringIO()
    explicit_digest = ""
    progress_digest = ""
    errored = False
    for raw in content.splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            message: object = json.loads(line)
        except json.JSONDecodeError:
            _ = output.write(line.decode("utf-8", "replace"))
            continue
        if not isinstance(message, dict):
            continue
        record = cast("dict[str, Any]", message)
        stream = record.get("stream")
        if isinstance(stream, str):
            _ = output.write(stream)
            found = _DIGEST_RE.findall(stream)
            if found:
                progress_digest = found[-1]
        explicit = record.get("digest")
        if isinstance(explicit, str) and explicit:
            explicit_digest = explicit
        error: object = record.get("error") or record.get("errorDetail")
        if error:
            errored = True
            _ = output.write(_error_text(error))
    return PushResult(
        success=not errored,
        digest=explicit_digest or progress_digest,
        output=output.getvalue(),
    )


def _registry_auth_header(auth: RegistryAuth) -> str:
    """Encode registry credentials into an `X-Registry-Auth` header value.

    libpod (like Docker) expects a base64url-encoded JSON auth config; only the
    username/password fields are needed for a basic-auth registry."""
    payload = json.dumps({"username": auth.username, "password": auth.password})
    return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii")


def build_async_client(socket_path: Path | str | None = None) -> httpx.AsyncClient:
    """Build an `httpx.AsyncClient` bound to the Podman Unix domain socket.

    The base URL host is a placeholder; the UDS transport ignores it and routes
    every request through the socket. Caller owns the client lifetime (use it as
    an async context manager, or pass it to `PodmanAdapter` and close it).
    """
    path = Path(socket_path) if socket_path else default_socket_path()
    transport = httpx.AsyncHTTPTransport(uds=str(path))
    return httpx.AsyncClient(transport=transport, base_url="http://podman")


# --- The concrete adapter ---------------------------------------------------


class PodmanAdapter:
    """`PodmanAPI` implemented against the real libpod HTTP API over `httpx`."""

    def __init__(
        self, client: httpx.AsyncClient, *, api_version: str = DEFAULT_API_VERSION
    ) -> None:
        self._client = client
        self._prefix = f"/{api_version}/libpod"
        self._api_version = api_version

    async def _send(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, str] | None = None,
        body: Mapping[str, Any] | None = None,
        ok: tuple[int, ...] = (200, 201, 204),
    ) -> httpx.Response:
        response = await self._client.request(method, path, params=params, json=body)
        if response.status_code not in ok:
            raise PodmanError(method, path, response.status_code, response.text)
        return response

    async def ping(self) -> bool:
        try:
            response = await self._client.get("/_ping")
        except httpx.HTTPError:
            return False
        return response.status_code == _HTTP_OK

    async def version(self) -> PodmanVersion:
        response = await self._send("GET", f"/{self._api_version}/version")
        data: dict[str, Any] = response.json()
        return PodmanVersion(
            version=str(data.get("Version", "")),
            api_version=str(data.get("ApiVersion", "")),
        )

    async def image_exists(self, reference: str) -> bool:
        response = await self._send(
            "GET", f"{self._prefix}/images/{reference}/exists", ok=(204, 404)
        )
        return response.status_code == _HTTP_NO_CONTENT

    async def pull_image(self, reference: str) -> None:
        # The pull response streams newline-delimited JSON progress; awaiting the
        # full body is enough to block until the pull finishes.
        _ = await self._send(
            "POST", f"{self._prefix}/images/pull", params={"reference": reference}
        )

    async def ensure_network(
        self, name: str, *, internal: bool, labels: Mapping[str, str]
    ) -> None:
        exists = await self._send(
            "GET", f"{self._prefix}/networks/{name}/exists", ok=(204, 404)
        )
        if exists.status_code == _HTTP_NO_CONTENT:
            return
        _ = await self._send(
            "POST",
            f"{self._prefix}/networks/create",
            body={"name": name, "internal": internal, "labels": dict(labels)},
        )

    async def create_pod(self, spec: PodSpec) -> str:
        body: dict[str, Any] = {"name": spec.name, "labels": dict(spec.labels)}
        if spec.networks:
            body["Networks"] = {name: {} for name in spec.networks}
        response = await self._send("POST", f"{self._prefix}/pods/create", body=body)
        return str(response.json()["Id"])

    async def start_pod(self, pod: str) -> None:
        _ = await self._send("POST", f"{self._prefix}/pods/{pod}/start")

    async def stop_pod(self, pod: str, *, grace_seconds: int | None = None) -> None:
        params = {"t": str(grace_seconds)} if grace_seconds is not None else None
        _ = await self._send(
            "POST", f"{self._prefix}/pods/{pod}/stop", params=params, ok=(200, 304)
        )

    async def remove_pod(self, pod: str, *, force: bool = True) -> None:
        # 404 means the pod is already gone, which is success for idempotent
        # cleanup.
        _ = await self._send(
            "DELETE",
            f"{self._prefix}/pods/{pod}",
            params={"force": _json_bool(value=force)},
            ok=(200, 204, 404),
        )

    async def list_pods(self, labels: Mapping[str, str]) -> list[ResourceSummary]:
        response = await self._send(
            "GET", f"{self._prefix}/pods/json", params=_label_filter(labels)
        )
        return [_summarize_pod(item) for item in response.json()]

    async def create_container(self, spec: ContainerSpec) -> str:
        response = await self._send(
            "POST", f"{self._prefix}/containers/create", body=_container_body(spec)
        )
        return str(response.json()["Id"])

    async def remove_container(self, container: str, *, force: bool = True) -> None:
        _ = await self._send(
            "DELETE",
            f"{self._prefix}/containers/{container}",
            params={"force": _json_bool(value=force)},
            ok=(200, 204, 404),
        )

    async def list_containers(self, labels: Mapping[str, str]) -> list[ResourceSummary]:
        params = {"all": "true", **_label_filter(labels)}
        response = await self._send(
            "GET", f"{self._prefix}/containers/json", params=params
        )
        return [_summarize_container(item) for item in response.json()]

    async def exec_create(self, container: str, spec: ExecSpec) -> str:
        body: dict[str, Any] = {
            "AttachStdout": True,
            "AttachStderr": True,
            "AttachStdin": False,
            "Tty": False,
            "Cmd": list(spec.command),
        }
        if spec.env:
            body["Env"] = [f"{key}={value}" for key, value in spec.env.items()]
        if spec.work_dir is not None:
            body["WorkingDir"] = spec.work_dir
        if spec.user is not None:
            body["User"] = spec.user
        response = await self._send(
            "POST", f"{self._prefix}/containers/{container}/exec", body=body
        )
        return str(response.json()["Id"])

    async def exec_start(self, exec_id: str) -> ExecOutput:
        # `/exec/{id}/start` hijacks the connection and streams the multiplexed
        # output. Reading the whole body to completion blocks until the command
        # exits, which is what `exec_step` wants.
        method = "POST"
        path = f"{self._prefix}/exec/{exec_id}/start"
        async with self._client.stream(
            method, path, json={"Detach": False, "Tty": False}
        ) as response:
            content = await response.aread()
        if response.status_code != _HTTP_OK:
            raise PodmanError(
                method, path, response.status_code, content.decode("utf-8", "replace")
            )
        return demultiplex_stream(content)

    async def exec_inspect(self, exec_id: str) -> ExecInspect:
        response = await self._send("GET", f"{self._prefix}/exec/{exec_id}/json")
        data: dict[str, Any] = response.json()
        exit_code = data.get("ExitCode")
        return ExecInspect(
            exit_code=int(exit_code) if exit_code is not None else -1,
            running=bool(data.get("Running", False)),
        )

    async def build_image(self, spec: BuildSpec) -> BuildResult:
        # `/build` takes the context as a tar request body and streams progress as
        # newline-delimited JSON; `networkmode=none` disables networking for `RUN`
        # unless the build opts in. A failed build still returns 200 with an
        # `error` record in the stream, so success is decided by the parser, not
        # the HTTP status.
        archive = await anyio.to_thread.run_sync(tar_build_context, spec.context_path)
        params: dict[str, str] = {
            "dockerfile": spec.dockerfile,
            "t": spec.tag,
            # Egress denied by default (ADR-0001); opting in lets RUN use the
            # default network namespace.
            "networkmode": "default" if spec.network else "none",
        }
        if spec.build_args:
            # libpod expects `buildargs` as a JSON-encoded object string.
            params["buildargs"] = json.dumps(dict(spec.build_args))
        if spec.target:
            params["target"] = spec.target
        method = "POST"
        path = f"{self._prefix}/build"
        async with self._client.stream(
            method,
            path,
            params=params,
            content=archive,
            headers={"Content-Type": "application/x-tar"},
        ) as response:
            content = await response.aread()
        if response.status_code != _HTTP_OK:
            raise PodmanError(
                method, path, response.status_code, content.decode("utf-8", "replace")
            )
        return parse_build_stream(content)

    async def push_image(self, spec: PushSpec) -> PushResult:
        # `/images/{name}/push` streams progress as newline-delimited JSON; a
        # failed push (bad/absent auth, unreachable registry) still returns 200
        # with an `error` record in the stream, so success is decided by the
        # parser, not the HTTP status. Auth rides in the `X-Registry-Auth` header
        # rather than the query string so the password never lands in a logged URL.
        params: dict[str, str] = {
            "destination": spec.destination,
            "tlsVerify": _json_bool(value=spec.tls_verify),
        }
        headers: dict[str, str] = {}
        if spec.auth is not None:
            headers["X-Registry-Auth"] = _registry_auth_header(spec.auth)
        method = "POST"
        path = f"{self._prefix}/images/{spec.source}/push"
        async with self._client.stream(
            method, path, params=params, headers=headers
        ) as response:
            content = await response.aread()
        if response.status_code != _HTTP_OK:
            raise PodmanError(
                method, path, response.status_code, content.decode("utf-8", "replace")
            )
        return parse_push_stream(content)


def _json_bool(*, value: bool) -> str:
    return "true" if value else "false"


def _label_filter(labels: Mapping[str, str]) -> dict[str, str]:
    """A libpod `filters` query param selecting resources by exact labels."""
    selectors = [f"{key}={value}" for key, value in labels.items()]
    return {"filters": json.dumps({"label": selectors})}


def _to_str(value: Any) -> str:
    """Coerce a JSON value (typed `Any`) to `str`, mapping `None` to empty."""
    return "" if value is None else str(value)


def _summarize_pod(item: Mapping[str, Any]) -> ResourceSummary:
    return ResourceSummary(
        id=_to_str(item.get("Id")),
        name=_to_str(item.get("Name")),
        labels=_str_map(item.get("Labels")),
    )


def _summarize_container(item: Mapping[str, Any]) -> ResourceSummary:
    names = item.get("Names")
    name = (
        _to_str(names[0])
        if isinstance(names, list) and names
        else _to_str(item.get("Id"))
    )
    return ResourceSummary(
        id=_to_str(item.get("Id")),
        name=name,
        labels=_str_map(item.get("Labels")),
    )


def _str_map(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    mapping = cast("Mapping[Any, Any]", value)
    return {_to_str(key): _to_str(item_value) for key, item_value in mapping.items()}


def _container_body(spec: ContainerSpec) -> dict[str, Any]:
    """Translate a `ContainerSpec` into a libpod SpecGenerator JSON body."""
    body: dict[str, Any] = {
        "name": spec.name,
        "image": spec.image,
        "pod": spec.pod,
        "labels": dict(spec.labels),
        "privileged": spec.privileged,
        "read_only_filesystem": spec.read_only_rootfs,
        "cap_drop": list(spec.cap_drop),
        "cap_add": list(spec.cap_add),
    }
    if spec.command is not None:
        body["command"] = list(spec.command)
    if spec.env:
        body["env"] = dict(spec.env)
    if spec.work_dir is not None:
        body["work_dir"] = spec.work_dir
    if spec.user is not None:
        body["user"] = spec.user
    if spec.no_new_privileges:
        body["security_opt"] = ["no-new-privileges"]
    # Explicit PID/IPC isolation rather than relying on Podman's defaults. The
    # network namespace comes from the pod's internal network, so it is not set
    # here.
    body["pidns"] = {"nsmode": "host" if spec.host_pid else "private"}
    body["ipcns"] = {"nsmode": "host" if spec.host_ipc else "private"}
    if spec.mounts:
        body["mounts"] = [
            {
                "type": "bind",
                "source": mount.source,
                "destination": mount.destination,
                "options": ["ro" if mount.read_only else "rw"],
            }
            for mount in spec.mounts
        ]
    resource_limits = _resource_limits(spec.limits)
    if resource_limits:
        body["resource_limits"] = resource_limits
    return body


def _resource_limits(limits: ResourceLimits | None) -> dict[str, Any]:
    if limits is None:
        return {}
    result: dict[str, Any] = {}
    if limits.cpu_quota is not None or limits.cpu_period is not None:
        cpu: dict[str, int] = {}
        if limits.cpu_quota is not None:
            cpu["quota"] = limits.cpu_quota
        if limits.cpu_period is not None:
            cpu["period"] = limits.cpu_period
        result["cpu"] = cpu
    if limits.memory_bytes is not None:
        result["memory"] = {"limit": limits.memory_bytes}
    if limits.pids_limit is not None:
        result["pids"] = {"limit": limits.pids_limit}
    return result
