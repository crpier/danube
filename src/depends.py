from collections.abc import Callable
from functools import lru_cache

import sqlalchemy.orm
from pydantic import BaseConfig
from sqlalchemy.orm import sessionmaker

from src.model import Base, get_engine

Session = Callable[[], sqlalchemy.orm.Session]


class Config(BaseConfig):
    SECRET_KEY: str
    HASH_ALGORITHM: str = "HS256"
    TOKEN_EXPIRE_MINUTES: int = 120
    DB_URI: str = "sqlite+pysqlite:///dev.db"


@lru_cache
def bootstrap() -> Config:
    config = Config()
    engine = get_engine(config.DB_URI)
    Base.metadata.create_all(engine)
    return config


def session() -> sessionmaker:
    config = bootstrap()
    engine = get_engine(config.DB_URI)

    return sessionmaker(engine)
