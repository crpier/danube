import logging
import sys
from collections.abc import Callable
from enum import StrEnum, auto
from typing import Annotated

import docker
import sqlalchemy.orm
from docker.types import LogConfig
from pydantic import BaseSettings
from sqlalchemy.orm import sessionmaker

from danube import docker_service, injector, tasks_service
from danube.model import Base, get_engine

Session = Annotated[Callable[[], sqlalchemy.orm.Session], injector.Injected]
DockerService = Annotated[docker_service.DockerService, injector.Injected]
TasksService = Annotated[tasks_service.TasksService, injector.Injected]


def bootstrap() -> None:
    config = Config.parse_obj({})
    injector.add_injectable("config", config)

    engine = get_engine(config.DB_URI)
    Base.metadata.create_all(engine)
    injector.add_injectable("session", sessionmaker(engine))

    injector.add_injectable(
        "docker_service",
        docker_service.DockerService(
            docker.from_env(),
            default_log_config=LogConfig(
                type=LogConfig.types.JSON,
                config={"max-size": "1g", "labels": "jobs"},
            ),
        ),
    )

    injector.add_injectable("tasks_service", tasks_service.TasksService())

    root_logger = logging.getLogger(config.APP_NAME)
    root_logger.setLevel(logging.INFO)

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(logging.INFO)
    formatter = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    handler.setFormatter(formatter)
    root_logger.addHandler(handler)
    injector.add_injectable("logger", root_logger)


class Environment(StrEnum):
    PROD = auto()
    DEV = auto()
    STAGING = auto()
    TEST = auto()


class Config(BaseSettings):
    APP_NAME: str = "danube"
    DB_URI: str = "sqlite+pysqlite:///dev.db"
    ENV: Environment = Environment.PROD
    HASH_ALGORITHM: str = "HS256"
    SECRET_KEY: str
    TOKEN_EXPIRE_MINUTES: int = 120

    class Config:
        env_file = ".env"
