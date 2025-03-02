from pathlib import Path
from lib.danube import Pipeline, Stage, pipeline
import pytest


@pipeline(image=Path("./Dockerfile"), auto_clone_checkout=True)
def run(pipeline: Pipeline):
    with Stage("linting", image="ghcr.io/astral-sh/ruff:0.9.9") as s:
        s.run("check .")
        s.run("format --check .")

    with Stage("testing") as s:
        s.run("echo pytest .")

        with Stage("coverage") as s:
            s.run("echo coverage report")

    with Stage("build") as s:
        s.run("echo uv build")

    if pipeline.on_default_branch:
        with Stage("push", image="docker:latest") as s:
            s.run("echo docker build -t danube-py3-none-any .")
