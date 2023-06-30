import datetime
from collections.abc import Callable
from enum import StrEnum, auto
from pathlib import Path
from typing import Any, TypedDict

command_result = tuple[str, str, int]


class PipelineConfig(TypedDict, total=False):
    max_time: datetime.timedelta


class Approvers(StrEnum):
    REPO_ADMINS = auto()
    REPO_WRITERS = auto()
    REPO_MAINTAINERS = auto()


class Pipeline:
    def __init__(
        self,
        DOCKER_IMAGES: dict[str, str | Path],
        CONFIG: PipelineConfig | None = None,
    ) -> None:
        self._docker_images = DOCKER_IMAGES
        self._config = CONFIG if CONFIG else {}
        self.BUILD_NUMBER = 0
        self.TARGET_DEPLOYMENTS: dict[str, str] = {}

    def stage(self, on: str | Path | None = None, clone: bool = False) -> Callable:
        class Stage:
            def __init__(self, func: Callable[[Any], None]) -> None:
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

    def set_stage_status(self, status: str) -> None:
        ...

    def prompt_confirmation(
        self,
        message: str,
        approvers: Approvers | list[Approvers],
    ) -> bool:
        ...

    def stop(self) -> None:
        ...

    def report_tests(self, suite_name: str, test_run: Any) -> None:
        ...


class Ops:
    def __init__(self, pipeline: Pipeline) -> None:
        self._pipeline = pipeline

    @staticmethod
    def shell(*args: Any, failhard: bool = True, **kwargs: Any) -> command_result:
        return ("shell", "code", 0)

    @staticmethod
    def git_clone():
        ...

    @staticmethod
    def git_commit(*args, **kwargs):
        ...

    @staticmethod
    def make(*args, failhard: bool = True, **kwargs) -> command_result:
        ...

    @staticmethod
    def docker_build(*args, **kwargs):
        ...

    @staticmethod
    def dokku_git_sync(*args, **kwargs):
        ...

    @staticmethod
    def ansible_playbook(*args, **kwargs):
        ...
