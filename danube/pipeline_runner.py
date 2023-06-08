import logging
import tarfile
from pathlib import Path

from danube.depends import DockerService, bootstrap
from danube.injector import injectable

bootstrap()

logger = logging.getLogger("display")


@injectable
def ensure_controller_image(
    *,
    docker_service: DockerService,
) -> None:
    with Path("danube/controller.Dockerfile").open("rb") as f:
        logs = docker_service.build_image(dockerfile=f, tag="danube-controller:latest")
    auxiliary: list[dict[str, str]] = []
    for log in logs:
        if msg := log.get("stream"):
            logger.info(msg.strip())
        elif aux := log.get("aux"):
            auxiliary.append(aux)
    logger.debug(auxiliary)


@injectable
def run_danube_file(danubefile: Path, *, docker_service: DockerService) -> None:
    tar_data = tarfile.open("archive.tar", "w")
    tar_data.add(danubefile, "danube.py")
    tar_data.close()
    container = docker_service.create_container(image="danube-controller:latest")
    print(container)

    with Path("archive.tar").open("rb") as f:
        container.put_archive("/home", f.read())


ensure_controller_image()

run_danube_file(Path("danube/example.py"))
