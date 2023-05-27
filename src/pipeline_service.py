from sqlalchemy import select

from src.depends import Session
from src.model import Pipeline
from src.schema import PipelineCreate, PipelineView


def get_pipelines(session: Session) -> list[PipelineView]:
    with session() as s:
        res = s.execute(select(Pipeline)).all()
        return [PipelineView.from_orm(pipeline[0]) for pipeline in res]


def create_pipeline(session: Session, pipeline_create: PipelineCreate) -> int:
    with session() as s:
        # TODO: can we make this implicit and type safe?
        new_pipeline = Pipeline(
            source_repo=pipeline_create.source_repo,
            pipeline_path=str(pipeline_create.pipeline_path),
        )
        s.add(new_pipeline)
        s.commit()
        return new_pipeline.id
