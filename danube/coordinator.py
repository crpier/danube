"""Coordinator entrypoint: boot the SDK, import the user `danubefile.py`, run it.

Runs inside the Danube-provided Coordinator container as
``python -m danube.coordinator`` (`docs/architecture/execution-model.md`, step 5).
It reads the SDK connection variables from the environment, loads the `pipeline`
coroutine from the danubefile, and runs it against a `DanubeClient`. Every
`step.run()` inside the pipeline reaches the Worker only through the Master RPC
path — there is no direct Coordinator -> Worker channel.

Exit contract (consumed by the Master via `wait_for_coordinator`):

- pipeline returns normally -> exit ``0``; the pipeline itself reports the job's
  terminal status (the sample danubefile calls ``status.report("success")``);
- pipeline raises -> best-effort ``status.report("failure")`` then exit ``1`` so a
  crash is visible to the Master even if the report never lands.
"""

import importlib.util
import logging
import os
from collections.abc import Awaitable, Callable
from contextlib import suppress
from pathlib import Path
from typing import cast

import anyio

from danube.sdk import DanubeClient, RpcError

logger = logging.getLogger("danube.coordinator")

# Path to the user pipeline inside the Coordinator container; the runner mounts
# the danubefile into the workspace, which both containers share at /workspace.
ENV_DANUBEFILE = "DANUBE_DANUBEFILE"
DEFAULT_DANUBEFILE = "/workspace/danubefile.py"

Pipeline = Callable[[DanubeClient], Awaitable[None]]


class DanubefileError(Exception):
    """The danubefile is missing or does not expose a callable `pipeline`."""


def load_pipeline(path: Path | str) -> Pipeline:
    """Import ``path`` as a module and return its `pipeline` coroutine function.

    Raises `DanubefileError` if the file is absent, cannot be imported, or has no
    callable `pipeline` attribute.
    """
    source = Path(path)
    if not source.is_file():
        msg = f"danubefile not found at {source}"
        raise DanubefileError(msg)
    spec = importlib.util.spec_from_file_location("danubefile", source)
    if spec is None or spec.loader is None:
        msg = f"could not load danubefile at {source}"
        raise DanubefileError(msg)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    pipeline = getattr(module, "pipeline", None)
    if not callable(pipeline):
        msg = f"danubefile at {source} defines no callable `pipeline`"
        raise DanubefileError(msg)
    return cast("Pipeline", pipeline)


async def run(client: DanubeClient, pipeline: Pipeline) -> int:
    """Run ``pipeline`` against ``client``; return the Coordinator exit code.

    Returns ``0`` on success and ``1`` on any pipeline exception, reporting the
    failure to the Master first (best-effort: a duplicate/late report that the
    Master rejects is swallowed so the crash exit code still surfaces).
    """
    try:
        await pipeline(client)
    except Exception as error:
        logger.exception("pipeline raised; reporting failure")
        with suppress(RpcError):
            _ = await client.status.report("failure", detail=str(error))
        return 1
    return 0


async def _main_async(path: str) -> int:
    pipeline = load_pipeline(path)
    async with DanubeClient.from_env() as client:
        return await run(client, pipeline)


def main() -> int:
    """Process entrypoint: resolve the danubefile path and run the pipeline."""
    path = os.environ.get(ENV_DANUBEFILE, DEFAULT_DANUBEFILE)
    return anyio.run(_main_async, path)


if __name__ == "__main__":
    raise SystemExit(main())
