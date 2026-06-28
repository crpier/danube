"""Read-only job endpoints: paginated list and single-job lookup."""

from fastapi import APIRouter, HTTPException, status
from snekql.sqlite import NoResultError, select

from danube.api.deps import DbDep, PageDep
from danube.api.schemas import JobResponse, Page
from danube.db.models import Job

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("")
async def list_jobs(db: DbDep, page: PageDep) -> Page[JobResponse]:
    """List jobs newest-first, paginated by `limit`/`offset`."""
    async with db.transaction() as tx:
        total = await tx.fetch_one(select(Job.id.count()).all())
        rows = await tx.fetch_all(
            select(Job)
            .all()
            .order_by(Job.created_at.desc(), Job.id.desc())
            .limit(page.limit)
            .offset(page.offset)
        )
    return Page(
        items=[JobResponse.model_validate(row) for row in rows],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get("/{job_id}")
async def get_job(job_id: str, db: DbDep) -> JobResponse:
    """Return a single job, or 404 if no job has that id."""
    async with db.transaction() as tx:
        try:
            row = await tx.fetch_one(select(Job).where(Job.id.eq(job_id)))
        except NoResultError:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"job {job_id!r} not found",
            ) from None
    return JobResponse.model_validate(row)
