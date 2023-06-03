from enum import StrEnum, auto
from typing import Annotated, TypedDict

from fastapi import APIRouter, Depends, FastAPI

from danube.depends import Session, session
from danube.pipeline_service import create_pipeline, get_pipelines
from danube.schema import PipelineCreate, PipelineView, UserCreate, UserId, UserView
from danube.user_service import create_user, get_users

app = FastAPI()
api_router = APIRouter()

# call session on startup to trigger any errors in lazy stuff
session()
db_session = Annotated[Session, Depends(session)]


class HealthStatus(StrEnum):
    HEALTHY = auto()
    BROKEN = auto()
    BOOTING = auto()


class HealthReport(TypedDict):
    status: HealthStatus


@api_router.get("/health")
def health_check() -> HealthReport:
    return {"status": HealthStatus.HEALTHY}


@api_router.get("/users")
def api_get_users(session: db_session) -> list[UserView]:
    return get_users(session=session)


@api_router.post("/users")
def api_create_user(session: db_session, new_user: UserCreate) -> UserId:
    return create_user(session=session, user_create=new_user)


@api_router.post("/pipelines")
def api_register_pipeline(session: db_session, new_pipeline: PipelineCreate) -> int:
    return create_pipeline(session=session, pipeline_create=new_pipeline)


@api_router.get("/pipelines")
def api_get_pipelines(session: db_session) -> list[PipelineView]:
    return get_pipelines(session=session)


app.include_router(api_router, prefix="/api/v1")