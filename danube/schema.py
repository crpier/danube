import datetime
from collections.abc import Callable
from pathlib import Path
from typing import NewType, TypedDict

from pydantic import BaseModel, EmailStr, HttpUrl, validator

UserId = NewType("UserId", int)
JobId = NewType("JobId", int)


class UserCreate(BaseModel):
    username: str
    email: EmailStr
    password: str


class UserView(BaseModel):
    id: UserId
    username: str
    email: EmailStr

    class Config:
        orm_mode = True


class PipelineCreate(BaseModel):
    name: str
    source_repo: HttpUrl
    script_path: Path | None = Path("danube.py")

    @validator("source_repo")
    def source_is_github(cls, v: HttpUrl) -> HttpUrl:  # noqa: N805
        # TODO: handle url with http://
        # TODO: handle ssh syntax url
        assert v.host == "github.com", "Source repo must be hosted on GitHub"
        return v

    @validator("script_path")
    def script_path_is_relative_and_python(cls, v: Path) -> Path:  # noqa: N805
        if v is None:
            return v
        if v.is_absolute():
            msg = "Path to script cannot be absolute"
            raise ValueError(msg)
        if v.suffix != ".py":
            msg = "Script must be a `.py` file"
            raise ValueError(msg)
        return v


class PipelineView(PipelineCreate):
    id: int

    class Config:
        orm_mode = True
