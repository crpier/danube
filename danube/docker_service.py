import logging
from io import BufferedReader
from typing import TYPE_CHECKING, TypedDict

import docker
from docker.models.images import Image
from docker.types import LogConfig

from docker.models.containers import Container


class DockerService:
    def __init__(
        self,
        client: docker.DockerClient,
        default_log_config: LogConfig,
    ) -> None:
        self._container_ids: list[str] = []
        self._log_config = default_log_config

        self._client = client
        self.logger = logging.getLogger()

    def start_container(self, image: str, env: dict[str, str | int]) -> str:
        # TODO: when detach=False, result is not a container; handle that
        container: Container = (  # type: ignore # noqa: PGH003
            self._client.containers.run(
                image=image,
                log_config=self._log_config,
                environment=env,
                detach=True,
                ports={"8000": "8000"},
            )
        )
        assert container.id
        self._container_ids.append(str(container.id))
        # TODO: add logging
        self.logger.info("Started container %s", container.id)
        return container.id

    def stop_container(self, id: str) -> None:
        result: Container = self._client.containers.get(  # type: ignore # noqa: PGH003
            id,
        )
        result.stop()

    def list_images(self) -> list[Image]:
        return self._client.images.list()  # type: ignore

    def build_image(
        # TODO: tag should be an object, or at least a new type
        self,
        dockerfile: BufferedReader,
        tag: str,
    ) -> list["BuildOutput"]:
        res: list[BuildOutput] = self._client.api.build(
            fileobj=dockerfile,
            tag=tag,
            decode=True,
            rm=True,
        )
        return res

    def create_container(self, image: str) -> Container:
        return self._client.containers.create(image=image)


class BuildOutput(TypedDict, total=False):
    stream: str
    aux: dict[str, str]
