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
        logs = docker_service.build_image(dockerfile=f, tag="latest")
    auxiliary: list[dict[str, str]] = []
    for log in logs:
        if msg := log.get("stream"):
            logger.info(msg.strip())
        elif aux := log.get("aux"):
            auxiliary.append(aux)
    logger.info(auxiliary)


@injectable
def run_danube_file(danubefile: Path, *, docker_service: DockerService) -> None:
    # Load the danubefile
    tar_data = tarfile.open("danubefile.tar", "w")
    tar_data.add(danubefile, "danubefile.py")
    tar_data.close()

    # Load the danube library
    tar_lib = tarfile.open("danube_lib.tar", "w")
    tar_lib.add(Path("danube"), "danube")
    tar_lib.close()

    container = docker_service.create_container(image="danube-controller:latest")
    logger.info(container)

    with Path("danubefile.tar").open("rb") as f:
        container.put_archive("/home", f.read())

    with Path("danube_lib.tar").open("rb") as f:
        container.put_archive("/home/danube", f.read())

    Path("danubefile.tar").unlink()
    res = container.start()
    logger.info(res)


ensure_controller_image()

# run_danube_file(Path("danube/example.py"))
