from pathlib import Path

from pydantic import BaseModel


class PipelineConfig(BaseModel):
    docker_images: list[str | Path]
    tasks: list[str]
