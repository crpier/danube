import datetime
from pathlib import Path
from typing import Any, Callable, TypedDict


class PipelineConfig(TypedDict, total=False):
    max_time: datetime.timedelta


class Pipeline:
    def __init__(
        self,
        DOCKER_IMAGES: dict[str, str | Path],
        CONFIG: PipelineConfig = {},
    ) -> None:
        self._docker_images = DOCKER_IMAGES
        self._config = CONFIG

    def stage(self, on: str | Path | None= None) -> Callable:
        class Stage:
            def __init__(self, func: Callable) -> None:
                self._func = func
            def __call__(self, *args: Any, **kwargs: Any) -> None:
                self._func(*args, **kwargs)
            def stage(self, func: Callable):
                return Stage(func)

        def inner(func):
            return Stage(func)
        return inner

    def register_stages(self, stages: Any) -> None:
        ...

    def set_stage_status(self, status: str)-> None:
        ...

    def prompt_confirmation(self, message: str)-> bool:
        ...

    def stop(self) -> None:
        ...

class Ops:
    def __init__(self, pipeline: Pipeline) -> None:
        self._pipeline = pipeline
    @staticmethod
    def shell(self, *args: Any, **kwargs: Any) -> tuple[str, str]:
        return ("shell", "code")
