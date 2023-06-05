from contextlib import asynccontextmanager
from enum import StrEnum, auto
from typing import TypedDict

from fastapi import APIRouter, FastAPI, HTTPException
from starlette.status import HTTP_400_BAD_REQUEST, HTTP_501_NOT_IMPLEMENTED

from danube.depends import bootstrap
from danube.pipeline_service import DuplicateError, create_pipeline, get_pipelines
from danube.schema import PipelineCreate, PipelineView, UserCreate, UserId, UserView
from danube.user_service import create_user, get_users


@asynccontextmanager
async def lifespan(app: FastAPI):
    bootstrap()
    yield


app = FastAPI(lifespan=lifespan)
api_router = APIRouter()


class GenericResponse(TypedDict):
    status: str
    message: str


class HealthStatus(StrEnum):
    HEALTHY = auto()
    BROKEN = auto()
    BOOTING = auto()


class HealthReport(TypedDict):
    status: HealthStatus


@api_router.get("/health")
async def health_check() -> HealthReport:
    return {"status": HealthStatus.HEALTHY}


@api_router.get("/users")
def api_get_users() -> list[UserView]:
    return get_users()


@api_router.post("/users")
def api_create_user(new_user: UserCreate) -> UserId:
    return create_user(user_create=new_user)


@api_router.post("/pipelines")
def api_register_pipeline(
    new_pipeline: PipelineCreate,
) -> GenericResponse:
    try:
        new_pipeline_id = create_pipeline(pipeline_create=new_pipeline)
        return {
            "status": "success",
            "message": f"Pipeline {new_pipeline.name} "
            f"created with id {new_pipeline_id}",
        }
    except DuplicateError:
        raise HTTPException(
            status_code=HTTP_400_BAD_REQUEST,
            detail=f"Pipeline with name {new_pipeline.name} already exists",
        ) from None
    except RuntimeError as e:
        raise HTTPException(status_code=HTTP_501_NOT_IMPLEMENTED, detail=str(e)) from e


@api_router.get("/pipelines")
def api_get_pipelines() -> list[PipelineView]:
    return get_pipelines()


@api_router.get("/tasks")
def get_tasks() -> str:
    return "tasks"


app.include_router(api_router, prefix="/api/v1")
