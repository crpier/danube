from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from danube.depends import DockerService, Session, TasksService
from danube.injector import injectable
from danube.model import Pipeline
from danube.schema import PipelineCreate, PipelineView


class DuplicateError(Exception):
    ...


@injectable
def get_pipelines(*, session: Session) -> list[PipelineView]:
    with session() as s:
        res = s.execute(select(Pipeline)).all()
        return [PipelineView.from_orm(pipeline[0]) for pipeline in res]


@injectable
def get_pipeline(pipeline_id: int, *, session: Session) -> PipelineView | None:
    with session() as s:
        res = s.execute(
            select(Pipeline).where(Pipeline.id == pipeline_id),
        ).one_or_none()
        if res:
            return PipelineView.from_orm(res[0])
        return None


@injectable
def create_pipeline(
    pipeline_create: PipelineCreate,
    *,
    session: Session,
    tasks_service: TasksService,
    docker_service: DockerService,
) -> int:
    with session() as s:
        # TODO: can we make this implicit and type safe?
        new_pipeline = Pipeline(
            source_repo=pipeline_create.source_repo,
            script_path=str(pipeline_create.script_path),
            name=pipeline_create.name,
        )
        s.add(new_pipeline)
        try:
            tasks_service.do_task(docker_service.build_image)
        except RuntimeError:
            s.rollback()
            raise
        try:
            s.commit()
        except IntegrityError as e:
            s.rollback()
            raise DuplicateError from e
        return new_pipeline.id


@injectable
def delete_pipeline(pipeline_id: int, *, session: Session) -> None:
    with session() as s:
        s.execute(delete(Pipeline).where(Pipeline.id == pipeline_id))
        s.commit()


@injectable
def delete_all_pipelines(*, session: Session) -> None:
    with session() as s:
        s.delete(select(Pipeline))
        s.commit()
