from pathlib import Path

import click
import click.decorators
import pydantic

from danube import schema
from danube.lib.pydantic_click import pyargument, pyoption


@click.group()
def cli() -> None:
    ...


#### Pipelines ###
@cli.group()
def pipeline() -> None:
    ...


@pipeline.command(name="add")
@pyargument("source-repo", schema=schema.PipelineCreate)
@pyoption("--script-path", schema=schema.PipelineCreate)
def add_pipeline(
    source_repo: pydantic.HttpUrl,
    script_path: Path | None,
) -> None:
    click.echo(source_repo)
    click.echo(script_path)
