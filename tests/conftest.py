import pytest
from sqlalchemy.orm import scoped_session, sessionmaker

from danube import docker_service, injector, tasks_service
from danube.depends import Config, Environment
from danube.model import Base, get_engine
from tests.test_docker_service import FakeDockerClient

MARKERS = {
    "unit": "test code with no IO",
    "component": " target only one individual component",
    "integration": " target multiple components",
    "acceptance": " target whole workflow when interfacing with the application",
}


def pytest_configure(config: pytest.Config) -> None:
    for marker_name, description in MARKERS.items():
        config.addinivalue_line("markers", f"{marker_name}: {description}")


def _pytest_itemcollected(item: pytest.Function) -> None:
    for marker in item.own_markers:
        if marker.name in MARKERS:
            break
    else:
        markers_list = " ".join([marker.name for marker in item.own_markers])
        msg = (
            f"Test {item} doesn't have any of the custom markers, "
            f"instead it has: {markers_list}"
        )
        raise ValueError(msg)


@pytest.fixture(autouse=True, scope="session")
def _fake_bootstrap() -> None:
    config = Config.parse_obj(
        {
            "APP_NAME": "danube",
            "DB_URI": "sqlite:///file:test?mode=memory&cache=shared&uri=true",
            "ENV": Environment.TEST,
            "SECRET_KEY": "test",
        },
    )
    injector.add_injectable("config", config)

    engine = get_engine(config.DB_URI)
    Base.metadata.create_all(engine)
    session = scoped_session(sessionmaker(engine))
    injector.add_injectable("session", session)

    injector.add_injectable(
        "docker_service",
        docker_service.DockerService(
            FakeDockerClient(),  # type: ignore
            {},  # type: ignore
        ),
    )

    injector.add_injectable("tasks_service", tasks_service.TasksService())


def get_test_session() -> scoped_session:
    return injector._INJECTS["session"]
