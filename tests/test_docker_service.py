import random
from typing import Any

import docker
import pytest
from docker.types import LogConfig

from danube.depends import Config, Environment
from danube.docker_service import DockerService


class FakeContainer:
    def __init__(self) -> None:
        self.id = "".join([random.choice("abc123") for _ in range(5)])


class FakeContainers:
    def __init__(self) -> None:
        self._containers: dict[str, FakeContainer] = {}
        self.run_log: list[dict[str, Any]] = []

    def run(self, **kwargs: dict[str, Any]) -> FakeContainer:
        self.run_log.append(kwargs)
        new_container = FakeContainer()
        self._containers[new_container.id] = new_container
        return new_container

    def get(self, id: str) -> FakeContainer:
        return self._containers[id]


class FakeDockerClient:
    def __init__(self) -> None:
        self.containers = FakeContainers()

    @staticmethod
    def from_env() -> "FakeDockerClient":
        return FakeDockerClient()


@pytest.fixture()
def fake_docker_client() -> FakeDockerClient:
    return FakeDockerClient()


@pytest.fixture()
def docker_service(fake_docker_client: FakeDockerClient) -> DockerService:
    return DockerService(fake_docker_client, {})  # type: ignore


@pytest.mark.unit()
def test_container_run_call_is_correct(
    docker_service: DockerService,
    fake_docker_client: FakeDockerClient,
) -> None:
    docker_service.run_container("test_image", {})
    expected_call = [
        {
            "image": "test_image",
            "log_config": {},
            "environment": {},
            "detach": True,
            "ports": {"8000": "8000"},
        },
    ]
    assert expected_call == fake_docker_client.containers.run_log


@pytest.mark.unit()
def test_container_id_saved_is_returned(
    docker_service: DockerService,
) -> None:
    container_id = docker_service.run_container("test_image", {})
    assert container_id == docker_service._containers[0]
