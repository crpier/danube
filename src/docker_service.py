import logging
from typing import TYPE_CHECKING

import docker
from docker.types import LogConfig

if TYPE_CHECKING:
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
