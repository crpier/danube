"""Read-only pipeline endpoints: paginated list and single-pipeline lookup."""

from fastapi import APIRouter, HTTPException, status
from snekql.sqlite import NoResultError, select

from danube.api.deps import DbDep, PageDep
from danube.api.schemas import Page, PipelineResponse
from danube.db.models import Pipeline

router = APIRouter(prefix="/pipelines", tags=["pipelines"])


@router.get("")
async def list_pipelines(db: DbDep, page: PageDep) -> Page[PipelineResponse]:
    """List pipelines newest-first, paginated by `limit`/`offset`."""
    async with db.transaction() as tx:
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


@router.get("/{pipeline_id}")
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
