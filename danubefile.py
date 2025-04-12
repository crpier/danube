import argparse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Annotated

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
    PYTHONPATH: Path


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

# Set up command line argument parsing
parser = argparse.ArgumentParser(description="Run the Danube pipeline")
parser.add_argument(
    "--dry-run",
    action="store_true",
    help="Run in dry-run mode (no actual commands executed)",
)

# Dynamically add arguments based on BuildParams
param_group = parser.add_argument_group("Build parameters")
build_params = MyPipeline.BuildParams.get_parameters()
for name, param_info in build_params.items():
    # Add different types of parameters based on their type
    if param_info["type"] == "bool":
        param_group.add_argument(
            f"--{name.lower()}",
            dest=name,
            action="store_true" if not param_info["default"] else "store_false",
            help=f"{param_info['description']} (default: {param_info['default']})",
        )

# Parse arguments
args = parser.parse_args()

# Extract parameter overrides from parsed arguments
param_overrides = {}
for name in build_params.keys():
    if hasattr(args, name) and getattr(args, name) is not None:
        param_overrides[name] = getattr(args, name)

# Run the pipeline using the provided configurations
with MyPipeline(
    image=BaseImage, dry_run=args.dry_run, param_overrides=param_overrides
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
            kind="confirm",
            description="Deploy?",
            timeout=10,
        )
        if not accepted:
            raise ExitStage("Deployment cancelled")
        api_key_value = pipeline.get_secret(secret="API_KEY")
        # Showing part of the api key for demo purposes only
        s.run(f"echo 'Deploying with API key: {api_key_value[:4]}...'")

    with Stage("Notify") as s:
        s.log("Sending notification to Slack")
        s.run("echo 'Deployment complete'")
