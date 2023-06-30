import logging
import tarfile
from pathlib import Path

from pydantic import HttpUrl, parse_obj_as

from danube import pipeline_service
from danube.depends import DockerService, Session, bootstrap
from danube.injector import injectable
from danube.schema import PipelineCreate

bootstrap()

logger = logging.getLogger("display")


@injectable
def ensure_controller_image(
    *,
    docker_service: DockerService,
) -> None:
    logs = docker_service.build_image(
        dockerfile_path="danube/controller.Dockerfile",
        tag="danube-controller:latest",
    )
    for log in logs:
        logger.info(log)


@injectable
def run_danube_file(danubefile: Path, *, docker_service: DockerService) -> None:
    # Load the danubefile
    tar_data = tarfile.open("danubefile.tar", "w")
    tar_data.add(danubefile, "danubefile.py")
    tar_data.close()

    container = docker_service.create_container(image="danube-controller:latest")
    logger.info(container)

    with Path("danubefile.tar").open("rb") as f:
        container.put_archive("/home", f.read())

    Path("danubefile.tar").unlink()
    res = container.start()
    logger.info(res)


@injectable
def add_pipeline(*, session: Session) -> None:
    pipeline_service.create_pipeline(
        PipelineCreate(
            name="example",
            source_repo=parse_obj_as(HttpUrl, "https://github.com/crpier/danube"),
            script_path=Path("danube/example.py"),
        ),
    )


ensure_controller_image()
run_danube_file(Path("danube/example.py"))
