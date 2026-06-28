"""Read-only pipeline endpoints: paginated list and single-pipeline lookup."""

from fastapi import APIRouter, Depends, HTTPException, status
from snekql.sqlite import NoResultError, select

from danube.api.deps import DbDep, PageDep, PrincipalDep, require_pipeline_read
from danube.api.schemas import Page, PipelineResponse
from danube.auth import readable_pipeline_ids
from danube.db.models import Pipeline

router = APIRouter(prefix="/pipelines", tags=["pipelines"])


@router.get("")
async def list_pipelines(
    db: DbDep, page: PageDep, principal: PrincipalDep
) -> Page[PipelineResponse]:
    """List pipelines newest-first, paginated by `limit`/`offset`.

    When auth is enabled, a non-admin caller sees only pipelines they may read;
    an admin (or the unauthenticated app) sees every pipeline.
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
                select(Pipeline.id.count()).where(Pipeline.id.in_(*pipeline_ids))
            )
            rows = await tx.fetch_all(
                select(Pipeline)
                .where(Pipeline.id.in_(*pipeline_ids))
                .order_by(Pipeline.created_at.desc(), Pipeline.id.desc())
                .limit(page.limit)
                .offset(page.offset)
            )
        else:
            total = await tx.fetch_one(select(Pipeline.id.count()).all())
            rows = await tx.fetch_all(
                select(Pipeline)
                .all()
                .order_by(Pipeline.created_at.desc(), Pipeline.id.desc())
                .limit(page.limit)
                .offset(page.offset)
            )
    return Page(
        items=[PipelineResponse.model_validate(row) for row in rows],
        total=total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get("/{pipeline_id}", dependencies=[Depends(require_pipeline_read)])
async def get_pipeline(pipeline_id: str, db: DbDep) -> PipelineResponse:
    """Return a single pipeline, or 404 if no pipeline has that id."""
    async with db.transaction() as tx:
        try:
            row = await tx.fetch_one(
                select(Pipeline).where(Pipeline.id.eq(pipeline_id))
            )
        except NoResultError:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"pipeline {pipeline_id!r} not found",
            ) from None
    return PipelineResponse.model_validate(row)
