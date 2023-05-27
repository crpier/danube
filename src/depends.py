import logging
import sys
from collections.abc import Callable
from enum import StrEnum, auto
from functools import lru_cache

import sqlalchemy.orm
from pydantic import BaseSettings
from sqlalchemy.orm import sessionmaker

from src.model import Base, get_engine

Session = Callable[[], sqlalchemy.orm.Session]


class Environment(StrEnum):
    PROD = auto()
    DEV = auto()
    STAGING = auto()
    TEST = auto()


class Config(BaseSettings):
    SECRET_KEY: str
    HASH_ALGORITHM: str = "HS256"
    TOKEN_EXPIRE_MINUTES: int = 120
    DB_URI: str = "sqlite+pysqlite:///dev.db"
    ENV: Environment = Environment.PROD


@lru_cache
def bootstrap() -> Config:
    config = Config.parse_obj({})
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(logging.DEBUG)
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    handler.setFormatter(formatter)
    root.addHandler(handler)

    engine = get_engine(config.DB_URI)
    Base.metadata.create_all(engine)
    return config


def session() -> sessionmaker:
    config = bootstrap()
    engine = get_engine(config.DB_URI)

    return sessionmaker(engine)
