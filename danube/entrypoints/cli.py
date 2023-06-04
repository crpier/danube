from pathlib import Path

import click
import click.decorators
import docker as docker_client
import pydantic
from docker.types import LogConfig

from danube import depends, docker_service, pipeline_service, schema
from danube.lib.pydantic_click import pyargument, pyoption

session = depends.session()
docker_manager = docker_service.DockerService(
    docker_client.from_env(),
    default_log_config=LogConfig(
        type=LogConfig.types.JSON,
        config={"max-size": "1g", "labels": "jobs"},
    ),
)


@click.group()
def cli() -> None:
    ...


#### Pipelines ###
@cli.group()
def pipeline() -> None:
    ...


@pipeline.command(name="add")
@pyargument("name", schema=schema.PipelineCreate)
@pyargument("source-repo", schema=schema.PipelineCreate)
@pyoption("--script-path", schema=schema.PipelineCreate)
@click.pass_context
def add_pipeline(
    ctx: click.Context,
    source_repo: pydantic.HttpUrl,
    script_path: Path | None,
    name: str,
) -> None:
    try:
        new_pipeline = schema.PipelineCreate(
            source_repo=source_repo,
            script_path=script_path,
            name=name,
        )
        pipeline_service.create_pipeline(session=session, pipeline_create=new_pipeline)
    except pipeline_service.DuplicateError:
        click.echo(f"Pipeline with {name=} already exists")
        ctx.exit(1)


@pipeline.command(name="list")
def list_pipelines() -> None:
    pipelines = pipeline_service.get_pipelines(session=session)
    for pipeline in pipelines:
        click.echo(f"{pipeline.name} {pipeline.source_repo} {pipeline.script_path}")


@pipeline.command(name="delete")
@click.option("--id", type=int)
@click.option(
    "--all",
    "-A",
    is_flag=True,
    default=False,
)
@click.pass_context
def delete_pipeline(
    ctx: click.Context,
    id: int | None,
    all: bool,
) -> None:
    if all:
        all = click.prompt(
            "Are you sure you want to delete all pipelines? [y/N]",
            default=False,
            prompt_suffix="",
            type=bool,
            show_default=False,
        )
    if all:
        pipeline_service.delete_all_pipelines(session=session)
        return

    if id is None:
        confirmed_id: int = click.prompt(
            "Which pipeline would you like to delete?",
            type=int,
            prompt_suffix="",
        )
    else:
        confirmed_id = id

    pipeline_to_delete = pipeline_service.get_pipeline(
        session=session,
        pipeline_id=confirmed_id,
    )
    if pipeline_to_delete is None:
        click.echo(f"Pipeline with id {confirmed_id} not found")
        ctx.exit(1)
    click.prompt(
        f"Deleting pipeline id {pipeline_to_delete.id} "
        f"for repo {pipeline_to_delete.source_repo}? [y/N]",
        default=False,
        prompt_suffix="",
        type=bool,
        show_default=False,
    )

    pipeline_service.delete_pipeline(session=session, pipeline_id=confirmed_id)


#### Docker stuff ###
@cli.group()
def docker() -> None:
    ...


@docker.group()
def image() -> None:
    ...


@image.command(name="list")
def list_images() -> None:
    images = docker_manager.list_images()
    for image in images:
        click.echo(f"{image.short_id} - {image.tags}")
