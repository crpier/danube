from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from danube.depends import Session
from danube.model import Pipeline
from danube.schema import PipelineCreate, PipelineView


class DuplicateError(Exception):
    ...


def get_pipelines(session: Session) -> list[PipelineView]:
    with session() as s:
        res = s.execute(select(Pipeline)).all()
        return [PipelineView.from_orm(pipeline[0]) for pipeline in res]


def get_pipeline(session: Session, pipeline_id: int) -> PipelineView | None:
    with session() as s:
        res = s.execute(
            select(Pipeline).where(Pipeline.id == pipeline_id),
        ).one_or_none()
        if res:
            return PipelineView.from_orm(res[0])
        return None


def create_pipeline(session: Session, pipeline_create: PipelineCreate) -> int:
    with session() as s:
        # TODO: can we make this implicit and type safe?
        new_pipeline = Pipeline(
            source_repo=pipeline_create.source_repo,
            script_path=str(pipeline_create.script_path),
            name=pipeline_create.name,
        )
        s.add(new_pipeline)
        try:
            s.commit()
            return new_pipeline.id
        except IntegrityError as e:
            s.rollback()
            raise DuplicateError from e


def delete_pipeline(session: Session, pipeline_id: int) -> None:
    with session() as s:
        s.execute(delete(Pipeline).where(Pipeline.id == pipeline_id))
        s.commit()


def delete_all_pipelines(session: Session) -> None:
    with session() as s:
        s.delete(select(Pipeline))
        s.commit()
