import os
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional, Type, TypeVar

from pydantic import BaseModel, Field, FilePath


class StageStatus(Enum):
    SUCCESS = "success"
    FAILURE = "failure"
    SKIPPED = "skipped"
    RUNNING = "running"
    PENDING = "pending"


T = TypeVar("T", bound=BaseModel)


class ExitStage(Exception): ...


class BoolParameter:
    """Parameter metadata for boolean values."""

    def __init__(self, description: str):
        self.description = description


class TriggersModel(BaseModel): ...


class BuildParamsModel(BaseModel): ...


class Image:
    def __init__(self, file: FilePath | None = None, name: str | None = None) -> None:
        pass


class DockerConfig(BaseModel):
    """Docker configuration for pipeline or stage."""

    image: str
    tag: str = "latest"
    env: Dict[str, str] = Field(default_factory=dict)
    volumes: List[str] = Field(default_factory=list)

    def __str__(self) -> str:
        return f"{self.image}:{self.tag}"


class Artifact(BaseModel):
    """Definition of an artifact to be stored."""

    name: str
    path: str
    retention: int = 5  # Number of builds to retain


class Secret(BaseModel):
    """Definition of a secret to be retrieved."""

    name: str
    key: str
    description: str = ""

    def __str__(self) -> str:
        return f"{self.name}/{self.key}"


class Pipeline:
    """Pipeline configuration and execution."""

    def __init__(
        self,
        image: Image,
        env_class: Type[BaseModel],
        trigger_class: Type[BaseModel],
        build_params: Type[BaseModel],
        docker: Optional[DockerConfig] = None,
    ):
        self.env = env_class()
        self.trigger = trigger_class()
        self.params = build_params()
        self.docker = docker
        self.artifacts: List[Artifact] = []
        self.secrets: Dict[str, str] = {}
        self.branch: str

    def __getattr__(self, name: str) -> Any:
        """Allow access to parameters using attribute syntax."""
        if hasattr(self.params, name):
            return getattr(self.params, name)
        return super().__getattr__(name)

    def __enter__(self):
        """Enter the pipeline context."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Exit the pipeline context."""
        # Store artifacts if pipeline completed successfully
        if exc_type is None:
            self._store_artifacts()

    def _store_artifacts(self):
        """Store artifacts defined in the pipeline."""
        for artifact in self.artifacts:
            log(f"Storing artifact: {artifact.name} from {artifact.path}")
            # Implementation would copy the artifact to a storage location
            # and manage retention policies

    def get_secret(self, secret: str) -> str:
        """Retrieve a secret value."""
        if secret.name not in self.secrets:
            log(f"Retrieving secret: {secret}")
            # In a real implementation, this would fetch from a secrets manager
            # For now, we'll use environment variables as a simple example
            secret_value = os.environ.get(f"{secret.name}_{secret.key}")
            if secret_value is None:
                raise ValueError(f"Secret not found: {secret}")
            self.secrets[secret.name] = secret_value
        return self.secrets[secret.name]


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

    def run(self, command: str) -> Result: ...

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


def artifact(name: str, path: str, retention: int = 5):
    """Define an artifact to be stored after the pipeline completes."""
    return Artifact(name=name, path=path, retention=retention)


def secret(name: str, key: str, description: str = ""):
    """Define a secret to be retrieved."""
    return Secret(name=name, key=key, description=description)
