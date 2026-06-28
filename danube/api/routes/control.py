"""Control-plane endpoints: trigger a run, cancel a job, stream its logs.

These write/streaming routes are mounted only when `create_app` is given a
`JobManager` (the read-only app omits them). Errors raised by the orchestrator map
to HTTP status codes:

- unknown pipeline on trigger -> 404
- unknown job on cancel/stream -> 404
- cancelling a job that is not running (still scheduling, or already finished) -> 409

The log stream is Server-Sent Events: existing log lines are replayed, then new
lines are tailed until the job reaches a terminal state.
"""

import json
from collections.abc import AsyncGenerator

import anyio
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse

from danube.api.deps import (
    JobManagerDep,
    require_job_read,
    require_job_write,
    require_pipeline_write,
)
from danube.api.schemas import JobResponse, RunPipelineRequest
from danube.domain.enums import JobStatus, TriggerType
from danube.domain.lifecycle import TERMINAL_STATES
from danube.orchestrator import (
    CannotCancelError,
    JobManager,
    JobNotFoundError,
    PipelineNotFoundError,
)

router = APIRouter(tags=["control"])

# How often the SSE stream re-reads the log file while the job is still running.
_LOG_POLL_INTERVAL_SECONDS = 0.1


@router.post(
    "/pipelines/{pipeline_id}/run",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_pipeline_write)],
)
async def run_pipeline(
    pipeline_id: str,
    manager: JobManagerDep,
    body: RunPipelineRequest | None = None,
) -> JobResponse:
    """Manually trigger a pipeline run; returns the created (pending) job."""
    ref = body.ref if body is not None else None
    try:
        job = await manager.trigger(pipeline_id, TriggerType.MANUAL, ref)
    except PipelineNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(error)
        ) from None
    return JobResponse.model_validate(job)


@router.post(
    "/jobs/{job_id}/cancel",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_job_write)],
)
async def cancel_job(job_id: str, manager: JobManagerDep) -> JobResponse:
    """Request cancellation of a running job; returns its current snapshot."""
    try:
        job = await manager.cancel(job_id)
    except JobNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(error)
        ) from None
    except CannotCancelError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(error)
        ) from None
    return JobResponse.model_validate(job)


@router.get("/jobs/{job_id}/logs/stream", dependencies=[Depends(require_job_read)])
async def stream_logs(job_id: str, manager: JobManagerDep) -> StreamingResponse:
    """Stream a job's log as Server-Sent Events: replay, then tail to terminal."""
    try:
        _ = await manager.fetch_job(job_id)
    except JobNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(error)
        ) from None
    return StreamingResponse(
        _log_events(manager, job_id), media_type="text/event-stream"
    )


def _consume_log_lines(buffer: bytes, *, flush: bool) -> tuple[list[str], int]:
    """Split `buffer` into whole log lines and report how many bytes were consumed.

    Only bytes up to (and including) the final newline are consumed, so a line —
    or a multibyte UTF-8 character — split across a read boundary is left in the
    file for the next read instead of being emitted as a partial `data:` frame or
    crashing the decode. When `flush` is set (the job is terminal and the writer
    has closed the file) any trailing bytes without a final newline are consumed
    and emitted as a last line. `errors="replace"` is defensive: complete lines
    are valid UTF-8, but a malformed byte must not kill the stream.
    """
    consumed = len(buffer) if flush else buffer.rfind(b"\n") + 1
    if consumed <= 0:
        return [], 0
    return buffer[:consumed].decode("utf-8", errors="replace").splitlines(), consumed


async def _log_events(manager: JobManager, job_id: str) -> AsyncGenerator[str]:
    """Yield SSE `data:` frames for each log line, ending once the job is terminal.

    Each pass reads the job status *before* the log file so that, on the pass that
    observes a terminal state, the file read that follows captures every line
    written before the log was closed (the writer flushes on every write and the
    session is closed before the job is finalized)."""
    path = anyio.Path(manager.log_path(job_id))
    offset = 0
    while True:
        job = await manager.fetch_job(job_id)
        terminal = JobStatus(job.status) in TERMINAL_STATES
        if await path.exists():
            async with await anyio.open_file(path, "rb") as handle:
                _ = await handle.seek(offset)
                chunk = await handle.read()
            lines, consumed = _consume_log_lines(chunk, flush=terminal)
            offset += consumed
            for line in lines:
                yield f"data: {line}\n\n"
        if terminal:
            payload = json.dumps({"status": str(job.status)})
            yield f"event: end\ndata: {payload}\n\n"
            return
        await anyio.sleep(_LOG_POLL_INTERVAL_SECONDS)
