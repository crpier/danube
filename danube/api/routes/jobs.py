"""Read-only job endpoints: paginated list and single-job lookup."""

from fastapi import APIRouter, Depends, HTTPException, status
from snekql.sqlite import NoResultError, select

from danube.api.deps import DbDep, PageDep, PrincipalDep, require_job_read
from danube.api.schemas import JobResponse, Page
from danube.auth import readable_pipeline_ids
from danube.db.models import Job

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("")
async def list_jobs(
    db: DbDep, page: PageDep, principal: PrincipalDep
) -> Page[JobResponse]:
    """List jobs newest-first, paginated by `limit`/`offset`.

    When auth is enabled, a non-admin caller sees only jobs of pipelines they may
    read; an admin (or the unauthenticated app) sees every job.
    """
    # Resolve the readable set before opening the listing transaction: the pool is
    # single-connection, so nesting a second transaction inside the first deadlocks.
    pipeline_ids: set[str] | None = None
    if principal is not None and not principal.is_global_admin:
        pipeline_ids = await readable_pipeline_ids(db, principal)
        if not pipeline_ids:
            return Page(items=[], total=0, limit=page.limit, offset=page.offset)
    async with db.transaction() as tx:
        if pipeline_ids is not None:
            total = await tx.fetch_one(
                select(Job.id.count()).where(Job.pipeline_id.in_(*pipeline_ids))
            )
            rows = await tx.fetch_all(
                select(Job)
                .where(Job.pipeline_id.in_(*pipeline_ids))
                .order_by(Job.created_at.desc(), Job.id.desc())
                .limit(page.limit)
                .offset(page.offset)
            )
        else:
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


@router.get("/{job_id}", dependencies=[Depends(require_job_read)])
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
