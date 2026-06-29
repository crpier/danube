"""httpx-backed RPC client and pipeline-facing facades for the Coordinator SDK.

`RpcClient` is the low-level transport: it posts JSON to the Master's `/rpc/*`
endpoints, attaches the job id to every body and the job-scoped token as a bearer
header, and turns non-2xx responses into `RpcError`. `DanubeClient` wraps it with
the three namespaces pipeline code uses — `step`, `secrets`, `artifacts` — plus
`status` for reporting the final job outcome.

The client is async: every call is a coroutine. Construct it directly with an
`httpx.AsyncClient` (the tests inject an ASGI transport) or via
`DanubeClient.from_env`, which reads the Coordinator's environment.
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Self, cast

import httpx

# Environment variables the Coordinator container is started with.
ENV_RPC_ADDRESS = "DANUBE_RPC_ADDRESS"
ENV_JOB_ID = "DANUBE_JOB_ID"
ENV_RPC_TOKEN = "DANUBE_RPC_TOKEN"  # noqa: S105 - env var name, not a credential
# Run metadata the Master plumbs in for the `danube.context` surface.
ENV_PIPELINE = "DANUBE_PIPELINE"
ENV_TRIGGER_TYPE = "DANUBE_TRIGGER_TYPE"
ENV_TRIGGER_REF = "DANUBE_TRIGGER_REF"


@dataclass(frozen=True, slots=True)
class JobContext:
    """Read-only run metadata for the current job, exposed as `danube.context`.

    `trigger_ref` is the raw ``branch/sha`` reference recorded on the job, or
    `None` for a run with no Git ref (e.g. a manual trigger). `branch` and `sha`
    decompose it: the sha is the final ``/``-separated segment, so a branch name
    containing slashes (``feature/foo``) is preserved. A ref with no ``/`` is
    not a valid ``branch/sha`` pair, so both `branch` and `sha` are `None`; with
    no ref at all, both are likewise `None`.
    """

    # Identity first, then the trigger_* pair grouped together (over strict
    # alphabetical ordering) since `trigger_ref` is what `branch`/`sha` derive
    # from and reads naturally beside `trigger_type`.
    job_id: str
    pipeline: str
    trigger_type: str
    trigger_ref: str | None

    @classmethod
    def from_mapping(cls, env: Mapping[str, str]) -> Self:
        """Build a context from a Coordinator environment mapping.

        `DANUBE_JOB_ID`, `DANUBE_PIPELINE`, and `DANUBE_TRIGGER_TYPE` are required;
        `DANUBE_TRIGGER_REF` is optional and absent for jobs with no Git ref.
        """
        return cls(
            job_id=env[ENV_JOB_ID],
            pipeline=env[ENV_PIPELINE],
            trigger_type=env[ENV_TRIGGER_TYPE],
            trigger_ref=env.get(ENV_TRIGGER_REF),
        )

    @property
    def branch(self) -> str | None:
        """The branch name from `trigger_ref`, or `None` when there is no ref."""
        if self.trigger_ref is None or "/" not in self.trigger_ref:
            return None
        return self.trigger_ref.rsplit("/", 1)[0]

    @property
    def sha(self) -> str | None:
        """The commit sha from `trigger_ref`, or `None` when there is no ref."""
        if self.trigger_ref is None or "/" not in self.trigger_ref:
            return None
        return self.trigger_ref.rsplit("/", 1)[1]


class RpcError(Exception):
    """An RPC call returned a non-success HTTP status."""

    def __init__(self, path: str, status_code: int, detail: str) -> None:
        self.path = path
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"RPC {path} failed with {status_code}: {detail}")


class StepError(Exception):
    """A `step.run(..., check=True)` command exited non-zero."""

    def __init__(self, command: str, exit_code: int) -> None:
        self.command = command
        self.exit_code = exit_code
        super().__init__(f"command {command!r} exited with code {exit_code}")


class BuildError(Exception):
    """An `images.build` failed (the Dockerfile did not build successfully)."""

    def __init__(self, tag: str) -> None:
        self.tag = tag
        super().__init__(f"image build for {tag!r} failed")


@dataclass(frozen=True, slots=True)
class StepResult:
    """Captured outcome of a step, returned by `step.capture`."""

    exit_code: int
    stdout: str
    stderr: str


@dataclass(frozen=True, slots=True)
class UploadedArtifact:
    """Metadata for an artifact the Master recorded."""

    artifact_id: str
    name: str
    size_bytes: int


@dataclass(frozen=True, slots=True)
class BuiltImage:
    """A container image produced by `images.build`.

    `tag` is the applied (raw, user-controlled) tag; `digest` is the image's
    id/digest in the host Local Image Store."""

    tag: str
    digest: str


class RpcClient:
    """Low-level JSON transport to the Master RPC control plane."""

    def __init__(self, http: httpx.AsyncClient, job_id: str, token: str) -> None:
        self._http = http
        self._job_id = job_id
        self._headers = {"Authorization": f"Bearer {token}"}

    async def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        """POST ``payload`` (with the job id injected) and return the JSON body."""
        body = {"job_id": self._job_id, **payload}
        response = await self._http.post(path, json=body, headers=self._headers)
        if response.is_error:
            raise RpcError(path, response.status_code, _detail(response))
        result: dict[str, Any] = response.json()
        return result


def _detail(response: httpx.Response) -> str:
    try:
        body: object = response.json()
    except ValueError:
        return response.text
    if isinstance(body, dict):
        # Safe: guarded by the isinstance check above; JSON object keys are str.
        mapping = cast("dict[str, object]", body)
        if "detail" in mapping:
            return str(mapping["detail"])
    return response.text


class StepApi:
    """`step` namespace: run commands in the Worker container.

    Each `run` executes in a **fresh shell** in the Worker — working directory and
    shell/environment variables do not carry over between calls
    (`docs/architecture/execution-model.md`, Stateless Shell Execution). Chain
    dependent commands in one call:

        # Wrong: the cd is lost before the next run
        await step.run("cd /app")
        await step.run("npm install")

        # Correct: chain with &&
        await step.run("cd /app && npm install")

    Environment variables are per call; pass them via ``env``:

        await step.run("echo $API_KEY", env={"API_KEY": await secrets.get("API_KEY")})
    """

    def __init__(self, client: RpcClient) -> None:
        self._client = client

    async def run(
        self,
        cmd: str,
        *,
        env: dict[str, str] | None = None,
        check: bool = True,
        name: str | None = None,
    ) -> int:
        """Run ``cmd`` in the Worker and return its exit code.

        With ``check=True`` (the default) a non-zero exit raises `StepError`; with
        ``check=False`` the exit code is returned and the pipeline continues.
        """
        data = await self._client.post(
            "/rpc/run-step",
            {"command": cmd, "env": env or {}, "name": name},
        )
        exit_code = int(data["exit_code"])
        if check and exit_code != 0:
            raise StepError(cmd, exit_code)
        return exit_code

    async def capture(
        self,
        command: str,
        *,
        env: dict[str, str] | None = None,
        check: bool = True,
        name: str | None = None,
    ) -> StepResult:
        """Run ``command`` and return its exit code together with captured output.

        Same stateless-shell and ``check`` semantics as `run`.
        """
        data = await self._client.post(
            "/rpc/run-step",
            {
                "command": command,
                "env": env or {},
                "name": name,
                "capture_output": True,
            },
        )
        exit_code = int(data["exit_code"])
        if check and exit_code != 0:
            raise StepError(command, exit_code)
        return StepResult(
            exit_code=exit_code,
            stdout=data.get("stdout") or "",
            stderr=data.get("stderr") or "",
        )


class ImagesApi:
    """`images` namespace: build container images on the host from the workspace.

    A build runs on the host's rootless Podman (the same daemon as the job pods),
    never inside the Worker, and lands in the host Local Image Store
    (`docs/adr/0001-host-side-image-build.md`). Tags are raw and user-controlled,
    so a pipeline disambiguates concurrent builds itself, typically with the Job
    Context commit sha:

        tag = f"myapp:{danube.context.sha or 'latest'}"
        image = await danube.images.build(tag)
    """

    def __init__(self, client: RpcClient) -> None:
        self._client = client

    async def build(  # noqa: PLR0913 - build options are idiomatic keyword args
        self,
        tag: str,
        *,
        context: str = ".",
        dockerfile: str = "Dockerfile",
        name: str | None = None,
        build_args: dict[str, str] | None = None,
        network: bool = False,
        target: str | None = None,
    ) -> BuiltImage:
        """Build and tag an image from ``context`` (a workspace-relative directory)
        using ``dockerfile`` within it, and return the `BuiltImage`.

        ``build_args`` populate the Containerfile's ``ARG`` instructions; they are
        **not** secret-safe, so never pass secrets through them. ``RUN`` networking
        is denied by default (`docs/adr/0001-host-side-image-build.md`); pass
        ``network=True`` to opt in. ``target`` selects a stage in a multi-stage
        Containerfile.

        A build whose Dockerfile fails raises `BuildError`; the failed `build` step
        is still recorded."""
        data = await self._client.post(
            "/rpc/build-image",
            {
                "tag": tag,
                "context": context,
                "dockerfile": dockerfile,
                "name": name,
                "build_args": build_args or {},
                "network": network,
                "target": target,
            },
        )
        if not data.get("success"):
            raise BuildError(tag)
        return BuiltImage(tag=str(data["tag"]), digest=str(data["digest"]))


class SecretsApi:
    """`secrets` namespace: fetch decrypted secrets the pipeline is authorized for."""

    def __init__(self, client: RpcClient) -> None:
        self._client = client

    async def get(self, key: str) -> str:
        """Return the secret value for ``key``, or raise `RpcError` if denied."""
        data = await self._client.post("/rpc/get-secret", {"key": key})
        return str(data["value"])


class ArtifactsApi:
    """`artifacts` namespace: upload build outputs from the job workspace."""

    def __init__(self, client: RpcClient) -> None:
        self._client = client

    async def upload(self, path: str, *, name: str) -> UploadedArtifact:
        """Register the artifact at workspace ``path`` (a file or directory) under
        ``name``. The Master copies the bytes out of the workspace and is the
        authority on the stored size; ``path`` is resolved relative to the job
        workspace, so a path that escapes it or does not exist is rejected."""
        data = await self._client.post(
            "/rpc/upload-artifact",
            {"path": path, "name": name},
        )
        return UploadedArtifact(
            artifact_id=str(data["artifact_id"]),
            name=str(data["name"]),
            size_bytes=int(data["size_bytes"]),
        )


class StatusApi:
    """`status` namespace: report the job's final outcome to the Master."""

    def __init__(self, client: RpcClient) -> None:
        self._client = client

    async def report(self, state: str, *, detail: str | None = None) -> str:
        """Report ``state`` (e.g. ``"success"``/``"failure"``) and return the
        Master's resulting job status."""
        data = await self._client.post(
            "/rpc/report-status", {"state": state, "detail": detail}
        )
        return str(data["status"])


class DanubeClient:
    """Pipeline-facing SDK facade: `context`, `step`, `images`, `secrets`,
    `artifacts`, `status`."""

    def __init__(
        self,
        http: httpx.AsyncClient,
        job_id: str,
        token: str,
        *,
        context: JobContext | None = None,
        owns_http: bool = False,
    ) -> None:
        self._http = http
        self._owns_http = owns_http
        # `context` is the run-metadata surface; when a caller constructs the
        # client directly without it (e.g. in-process tests injecting a transport),
        # fall back to a ref-less context that still carries the job id.
        self.context = context or JobContext(
            job_id=job_id, pipeline="", trigger_type="", trigger_ref=None
        )
        client = RpcClient(http, job_id, token)
        self.step = StepApi(client)
        self.images = ImagesApi(client)
        self.secrets = SecretsApi(client)
        self.artifacts = ArtifactsApi(client)
        self.status = StatusApi(client)

    @classmethod
    def from_env(cls) -> Self:
        """Build a client from the Coordinator's environment variables.

        Reads `DANUBE_RPC_ADDRESS`, `DANUBE_JOB_ID`, and `DANUBE_RPC_TOKEN` for the
        transport, plus the `danube.context` run metadata, and owns the underlying
        `httpx.AsyncClient` (closed by `aclose`).
        """
        address = os.environ[ENV_RPC_ADDRESS]
        job_id = os.environ[ENV_JOB_ID]
        token = os.environ[ENV_RPC_TOKEN]
        context = JobContext.from_mapping(os.environ)
        http = httpx.AsyncClient(base_url=address)
        return cls(http, job_id, token, context=context, owns_http=True)

    async def aclose(self) -> None:
        """Close the underlying HTTP client if this facade owns it."""
        if self._owns_http:
            await self._http.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()
