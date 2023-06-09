from datetime import datetime
from enum import StrEnum, auto
from pathlib import Path
from typing import Any

import sqlalchemy.engine
from sqlalchemy import Engine, ForeignKey
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def get_engine(
    uri: str = "sqlite+pysqlite:///dev.db",
    **kwargs: Any,
) -> Engine:
    return sqlalchemy.engine.create_engine(uri, **kwargs)


class JobStatus(StrEnum):
    IN_PROGRESS = auto()
    SUCCESS = auto()
    FAILURE = auto()
    ERROR = auto()


class Base(DeclarativeBase):
    ...


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str]
    email: Mapped[str]
    pass_hash: Mapped[str]


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    status: Mapped[JobStatus]
    started_by: Mapped[int]
    started_at: Mapped[datetime]
    stopped_at: Mapped[datetime | None]


class Pipeline(Base):
    __tablename__ = "pipelines"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(unique=True)
    source_repo: Mapped[str]
    script_path: Mapped[str] = mapped_column(default="danube.py")
