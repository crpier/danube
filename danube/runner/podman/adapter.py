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

import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, cast, runtime_checkable

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


def _json_bool(*, value: bool) -> str:
    return "true" if value else "false"


def _label_filter(labels: Mapping[str, str]) -> dict[str, str]:
    """A libpod `filters` query param selecting resources by exact labels."""
    selectors = [f"{key}={value}" for key, value in labels.items()]
    return {"filters": json.dumps({"label": selectors})}


def _s(value: Any) -> str:
    """Coerce a JSON value (typed `Any`) to `str`, mapping `None` to empty."""
    return "" if value is None else str(value)


def _summarize_pod(item: Mapping[str, Any]) -> ResourceSummary:
    return ResourceSummary(
        id=_s(item.get("Id")),
        name=_s(item.get("Name")),
        labels=_str_map(item.get("Labels")),
    )


def _summarize_container(item: Mapping[str, Any]) -> ResourceSummary:
    names = item.get("Names")
    name = _s(names[0]) if isinstance(names, list) and names else _s(item.get("Id"))
    return ResourceSummary(
        id=_s(item.get("Id")),
        name=name,
        labels=_str_map(item.get("Labels")),
    )


def _str_map(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    mapping = cast("Mapping[Any, Any]", value)
    return {_s(key): _s(val) for key, val in mapping.items()}


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
