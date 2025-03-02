import functools
from contextlib import contextmanager
from typing import Callable, Optional, Union
from pathlib import Path

from danube.runner import DanubeAPI


class Pipeline:
    def __init__(self, api: DanubeAPI, image: Union[str, Path], auto_clone_checkout: bool):
        self.api = api
        self.image = image
        self.auto_clone_checkout = auto_clone_checkout
        self.on_default_branch = self.api.is_default_branch()
        self.built_image = None

        if isinstance(self.image, Path):
            self.build_image()

    def log(self, message: str):
        self.api.log(message)

    def build_image(self):
        if isinstance(self.image, Path):
            self.log(f"Building Docker image from Dockerfile: {self.image}")
            self.built_image = self.api.build_image(self.image)
            self.log(f"Built image: {self.built_image}")

_current_pipeline: Pipeline = None

import uuid

class Stage:
    def __init__(self, name: str, image: Optional[Union[str, Path]] = None, api: Optional[DanubeAPI] = None):
        self.name = name
        self.image = image
        if api is None:
            self.api = _current_pipeline.api
        else:
            self.api = api
        self.container_name = f"danube-{self.name}-{uuid.uuid4().hex[:8]}"

    def __enter__(self):
        self.api.set_current_stage(self.name)
        self.api.log(f"Starting stage: {self.name}")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.api.log(f"Finished stage: {self.name}")
        if exc_type:
            self.api.log(f"Stage {self.name} failed: {exc_val}")
        self.api.set_current_stage(None)

    def run(self, command: str | Callable):
        image = self.image or _current_pipeline.built_image or _current_pipeline.image
        if callable(command):
            self.api.log(f"Running Python function: {command.__name__}")
            result = command()
            self.api.log(f"Function result: {result}")
        else:
            self.api.log(f"Running command: {command}")
            container_id = self.api.run_command(image, command, self.container_name)
            self.api.get_logs(container_id, self.container_name)


def pipeline(image: Union[str, Path], auto_clone_checkout: bool = False):
    def decorator(func: Callable):
        @functools.wraps(func)
        def wrapper(api):
            api.default_image = image if isinstance(image, str) else None
            pipeline_instance = Pipeline(api, image, auto_clone_checkout)
            global _current_pipeline
            _current_pipeline = pipeline_instance

            if auto_clone_checkout:
                api.log("Auto cloning and checking out repository")
                # Implement auto clone and checkout logic here

            return func(pipeline_instance)

        return wrapper

    return decorator
