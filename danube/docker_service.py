import logging
from typing import TypedDict

import docker
from docker.models.containers import Container
from docker.models.images import Image


class DockerService:
    def __init__(
        self,
        client: docker.DockerClient,
    ) -> None:
        self._containers: dict[str, Container] = {}

        self._client = client
        self.logger = logging.getLogger()

    def run_container(self, image: str, env: dict[str, str | int]) -> str:
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
        self._containers[str(container.id)] = container
        # TODO: add logging
        self.logger.info("Started container %s", container.id)
        return container.id

    def stop_container(self, id: str) -> None:
        result: Container = self._client.containers.get(  # type: ignore # noqa: PGH003
            id,
        )
        result.stop()

    def list_images(self) -> list[Image]:
        return self._client.images.list()

    def build_image(
        # TODO: tag should be an object, or at least a new type
        self,
        dockerfile_path: str,
        tag: str,
        context: str = ".",
    ) -> list["BuildOutput"]:
        res: list[BuildOutput] = self._client.api.build(
            path=context,
            dockerfile=dockerfile_path,
            tag=tag,
            decode=True,
            rm=True,
        )
        return res

    def create_container(self, image: str) -> Container:
        new_container: Container = self._client.containers.create(
            image=image,
            detach=True,
            ports={"8000": "8000"},
            volumes={
                "/var/run/docker.sock": {"bind": "/var/run/docker.sock", "mode": "rw"},
            },
        )
        self._containers[str(new_container.id)] = new_container
        return new_container


class BuildOutput(TypedDict, total=False):
    stream: str
    aux: dict[str, str]
