from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Annotated

from pydantic import FilePath

from lib import (
    BoolBuildParameter,
    BuildParamsModel,
    EnvironmentModel,
    ExitStage,
    Image,
    Pipeline,
    Stage,
    TriggersModel,
)


class Environment(EnvironmentModel):
    PYTHONPATH: FilePath


class Triggers(TriggersModel):
    branches: list[str] = ["^main$", "^dev$", "^feat:.*$"]
    events: list[str] = ["push", "pull_request"]


class BuildParams(BuildParamsModel):
    DEPLOY: Annotated[
        bool,
        BoolBuildParameter(description="Whether deployment requires approval"),
    ] = True


class MyPipeline(Pipeline):
    Environment = Environment
    Triggers = Triggers
    BuildParams = BuildParams


BaseImage = Image(file=Path("./Dockerfile"))
"""Basic image for most tasks"""
BuildImage = Image(name="python:3.11-slim")
"""Image for build and deploy actions"""

with MyPipeline(
    image=BaseImage,
) as pipeline:
    with Stage("Sanity checks") as s, ThreadPoolExecutor(max_workers=2) as pool:
        pool.submit(lambda: s.run("ruff check ."))
        pool.submit(lambda: s.run("pyright ."))

    with Stage("Test", timeout=100) as s:
        res = s.run("coverage run -m pytest tests")
        if res.returncode != 0:
            raise ExitStage("Tests failed")
        s.run("coverage html")
        pipeline.save_artifact(name="coverage-report", path="htmlcov/index.html")

    if pipeline.branch != "main":
        exit()

    with Stage("Build", image=BuildImage):
        s.run("uv build")
        pipeline.save_artifact(name="dist-package", path="dist/*.whl")

    if not pipeline.BuildParams.DEPLOY:
        exit()

    with Stage("Deploy", image=BuildImage) as s:
        accepted = s.input(
            type="confirm",
            name="deploy",
            description="Deploy?",
            timeout=10,
        )
        if not accepted:
            raise ExitStage("Deployment cancelled")
        api_key_value = pipeline.get_secret("API_KEY")
        # Showing part of the api key for demo purposes only
        s.run(f"echo 'Deploying with API key: {api_key_value[:4]}...'")

    with Stage("Notify") as s:
        s.log("Sending notification to Slack")
        s.run("echo 'Deployment complete'")
