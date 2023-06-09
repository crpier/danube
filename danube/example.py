import datetime
import time
from pathlib import Path

from danube.pipeline_config import Approvers, Ops, Pipeline

DOCKER_IMAGES: dict[str, str | Path] = {
    "danube-dev": Path("build/dev.Dockerfile"),
    "danube-prod": "danube-prod:latest",
}

pipeline = Pipeline(
    DOCKER_IMAGES=DOCKER_IMAGES,
    CONFIG={"max_time": datetime.timedelta(hours=1)},
)
ops = Ops(pipeline)


@pipeline.stage(on=DOCKER_IMAGES["danube-dev"], clone=True)
def lint(pipeline: Pipeline, ops: Ops):
    _, _, retcode = ops.make("lint", failhard=False)
    if retcode != 0:
        ops.make("fix_linting")
        ops.git_commit(f"Fix linting in build {pipeline.BUILD_NUMBER}")


@pipeline.stage(on=DOCKER_IMAGES["danube-dev"], clone=True)
def unit_test(ops: Ops):
    pipeline.report_tests("Unit Tests", ops.make("test_unit"))


@pipeline.stage()
def build(ops: Ops):
    ops.docker_build(Path("Dockerfile"), tag="latest")


@pipeline.stage()
def deploy_staging():
    ops.dokku_git_sync(pipeline.TARGET_DEPLOYMENTS["staging"])


@pipeline.stage()
def acceptance_test(ops: Ops):
    pipeline.report_tests("Acceptance Tests", ops.make("test_acceptance"))


@pipeline.stage()
def deploy_production():
    if pipeline.prompt_confirmation(
        "Are you sure you want to deploy to production?",
        approvers=Approvers.REPO_ADMINS,
    ):
        ops.dokku_git_sync(pipeline.TARGET_DEPLOYMENTS["production"])


time.sleep(10)
