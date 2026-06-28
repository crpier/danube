"""Artifact download endpoints: per-job listing and single-artifact streaming.

Artifacts are captured into ``<data_dir>/artifacts/<job_id>/<name>`` by the RPC
control plane while the job is still running (`docs/architecture/execution-model.md`,
Artifact Upload). These read-only routes expose what was stored: a listing of a
job's artifacts and a download of one by name. A file artifact streams its bytes
verbatim; a directory artifact streams a tar archive of the tree.
"""

import tarfile
import tempfile
from pathlib import Path

import anyio
import anyio.to_thread
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse, Response
from snekql.sqlite import NoResultError, select
from starlette.background import BackgroundTask

from danube.api.deps import DbDep, require_job_read
from danube.api.schemas import ArtifactResponse
from danube.db.models import Artifact, Job

router = APIRouter(prefix="/artifacts", tags=["artifacts"])


@router.get("/{job_id}", dependencies=[Depends(require_job_read)])
async def list_artifacts(job_id: str, db: DbDep) -> list[ArtifactResponse]:
    """List a job's artifacts by name; 404 if no job has that id.

    An empty list means the job ran but produced no artifacts, kept distinct from
    an unknown job so a client can tell the two apart.
    """
    async with db.transaction() as tx:
        try:
            _ = await tx.fetch_one(select(Job.id).where(Job.id.eq(job_id)))
        except NoResultError:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"job {job_id!r} not found",
            ) from None
        rows = await tx.fetch_all(
            select(Artifact)
            .where(Artifact.job_id.eq(job_id))
            .order_by(Artifact.name.asc())
        )
    return [ArtifactResponse.model_validate(row) for row in rows]


@router.get("/{job_id}/{name}", dependencies=[Depends(require_job_read)])
async def download_artifact(job_id: str, name: str, db: DbDep) -> Response:
    """Stream one stored artifact: the file bytes, or a tar of a directory tree."""
    async with db.transaction() as tx:
        try:
            row = await tx.fetch_one(
                select(Artifact).where(
                    Artifact.job_id.eq(job_id) & Artifact.name.eq(name)
                )
            )
        except NoResultError:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"artifact {name!r} not found for job {job_id!r}",
            ) from None
    stored = anyio.Path(row.path)
    if not await stored.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"artifact {name!r} bytes are no longer available",
        )
    if await stored.is_dir():
        archive = await anyio.to_thread.run_sync(_tar_directory, row.path)
        return FileResponse(
            archive,
            media_type="application/x-tar",
            filename=f"{name}.tar",
            # The tar is a throwaway packed for this response; drop it once sent.
            background=BackgroundTask(_unlink, archive),
        )
    return FileResponse(row.path, filename=name)


def _tar_directory(path: str) -> str:
    """Pack the directory tree at ``path`` into a temporary tar file, returning its
    path.

    Entries are stored relative to the directory root so the archive unpacks to the
    artifact's contents without the absolute storage path leaking in. The archive is
    written to disk rather than memory so a large directory artifact does not have to
    be buffered whole. Runs in a worker thread off the event loop.
    """
    with tempfile.NamedTemporaryFile(
        suffix=".tar", prefix="danube-artifact-", delete=False
    ) as buffer:
        with tarfile.open(fileobj=buffer, mode="w") as archive:
            archive.add(path, arcname="")
        return buffer.name


def _unlink(path: str) -> None:
    """Delete a temporary archive once its response has been sent."""
    Path(path).unlink(missing_ok=True)
