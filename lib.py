from dataclasses import dataclass
from pathlib import Path
from typing import Any, List, Optional
from unittest.mock import MagicMock

from pydantic import BaseModel


class ExitStage(Exception): ...


class BoolBuildParameter:
    def __init__(self, description: str):
        self.description = description


class EnvironmentModel(BaseModel): ...


class TriggersModel(BaseModel): ...


class BuildParamsModel(BaseModel): ...


class Image(BaseModel):
    file: Path | None = None
    name: str | None = None


class Secret(BaseModel):
    """Definition of a secret to be retrieved."""

    name: str
    key: str
    description: str = ""

    def __str__(self) -> str:
        return f"{self.name}/{self.key}"


class Pipeline:
    """Pipeline configuration and execution."""

    def __init__(self, image: Image | None = None):
        self.image = image

    def get_secret(self, secret: str) -> str: ...

    @property
    def branch(self) -> str: ...

    def save_artifact(self, name: str, path: str): ...

    def __enter__(self):
        """Enter the pipeline context."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb): ...


@dataclass
class Result:
    stdout: str
    stderr: str
    returncode: int


class Stage:
    def __init__(self, name: str, *, image: Image | None = None, timeout: int = 0):
        self.name = name
        self.image = image

    def __enter__(self):
        """Enter the stage context."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Exit the stage context."""

    def run(self, command: str) -> Result:
        return MagicMock(returncode=0)

    def log(self, message: str):
        """Log a message to the console."""
        print(f"[Danube] {message}")

    def input(
        self,
        type: str = "text",
        name: str = "input",
        description: str = "",
        timeout: int = 300,
    ) -> str: ...


class Parameter:
    """Base class for pipeline parameters with validation."""

    def __init__(
        self, name: str, description: str, default: Any = None, required: bool = False
    ):
        self.name = name
        self.description = description
        self._value = default
        self.required = required

    @property
    def value(self) -> Any:
        return self._value

    @value.setter
    def value(self, val: Any):
        self.validate(val)
        self._value = val

    def validate(self, value: Any) -> None:
        pass


@dataclass
class StringParameter(Parameter):
    choices: Optional[List[str]] = None

    def validate(self, value: Any) -> None:
        if not isinstance(value, str):
            raise ValueError(f"{self.name} must be a string")
        if self.choices and value not in self.choices:
            raise ValueError(f"{self.name} must be one of {self.choices}")


def secret(name: str, key: str, description: str = ""):
    """Define a secret to be retrieved."""
    return Secret(name=name, key=key, description=description)
