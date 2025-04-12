import os
import typing
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, List, Optional

from pydantic import BaseModel


class ExitStage(Exception): ...


class BoolBuildParameter:
    """Annotation class for boolean build parameters.

    Used to provide metadata for boolean parameters in BuildParamsModel subclasses.
    """

    def __init__(self, description: str):
        self.description = description

    def __repr__(self) -> str:
        return f"BoolBuildParameter(description='{self.description}')"


class EnvironmentModel(BaseModel):
    """Base model for environment variables configuration.

    Subclass this and add environment variables as strongly-typed class attributes.
    """

    def __init__(self, **kwargs):
        """Initialize the environment model.

        Reads values for class attributes from environment variables with the same name.
        """
        # Get type hints for the class to determine which attributes are defined
        type_hints = typing.get_type_hints(self.__class__)

        # Create a dictionary for environment values
        env_values = {}

        # Read values from environment variables for each defined attribute
        for attr_name, attr_type in type_hints.items():
            if not attr_name.startswith("_") and attr_name not in kwargs:
                env_value = os.environ.get(attr_name)
                if env_value is not None:
                    try:
                        # Try to convert the environment variable to the expected type
                        if attr_type is str:
                            env_values[attr_name] = env_value
                        elif attr_type is int:
                            env_values[attr_name] = int(env_value)
                        elif attr_type is float:
                            env_values[attr_name] = float(env_value)
                        elif attr_type is bool:
                            env_values[attr_name] = env_value.lower() in (
                                "true",
                                "t",
                                "yes",
                                "y",
                                "1",
                            )
                        else:
                            # For other types, try direct conversion
                            env_values[attr_name] = attr_type(env_value)
                    except (ValueError, TypeError):
                        # If conversion fails, skip this attribute
                        pass

        # Initialize with environment values, then override with kwargs
        super().__init__(**{**env_values, **kwargs})

    def dict(self):  # pyright: ignore[reportIncompatibleMethodOverride]
        """Return environment variables as a dictionary."""
        return {k: str(v) for k, v in self.__dict__.items() if not k.startswith("_")}
        """Initialize the model by reading values from environment variables.

        This constructor will:
        1. Look for environment variables matching each field name
        2. Use those values to initialize the model
        3. Override with any values provided as keyword arguments
        """
        # Create a dictionary of values from environment variables
        env_values = {}

        # Get field names from class annotations (these are the defined fields)
        for field_name in self.__class__.__annotations__:
            if field_name.startswith("_"):
                continue

            # Look for the environment variable with the same name as the field
            env_value = os.environ.get(field_name)
            if env_value is not None:
                env_values[field_name] = env_value

        # Override with any explicitly provided values
        env_values.update(data)

        # Initialize the model with the values, letting Pydantic handle type conversion
        super().__init__(**env_values)

    def dict(self):  # pyright: ignore[reportIncompatibleMethodOverride]
        """Return environment variables as a dictionary."""
        return {k: str(v) for k, v in self.__dict__.items() if not k.startswith("_")}


class TriggersModel(BaseModel):
    """Base model for pipeline triggers configuration.

    Defines conditions for when the pipeline should run.
    """

    branches: list[str] = []
    events: list[str] = ["push"]

    def matches_branch(self, branch_name: str) -> bool:
        """Check if a branch name matches any of the patterns in branches."""
        import re

        return any(re.match(pattern, branch_name) for pattern in self.branches)


class BuildParamsModel(BaseModel):
    """Base model for build parameters configuration.

    Subclass this and add parameters as annotated class attributes.
    """

    @classmethod
    def get_parameters(cls):
        """Extract metadata from annotations."""
        from typing import get_args, get_origin, get_type_hints

        hints = get_type_hints(cls, include_extras=True)
        params = {}

        for name, hint in hints.items():
            if get_origin(hint) is Annotated:
                args = get_args(hint)
                __type_arg__ = args[0]  # noqa: F841
                metadata = args[1:]

                for meta in metadata:
                    if isinstance(meta, BoolBuildParameter):
                        params[name] = {
                            "type": "bool",
                            "description": meta.description,
                            "default": getattr(cls, name, None),
                        }

        return params


class Image(BaseModel):
    """Definition of a container image to be used in a pipeline or stage.

    An image can be defined either by a Dockerfile path or by a reference name.
    """

    file: Path | None = None
    name: str | None = None

    def __str__(self) -> str:
        if self.name:
            return f"Image(name='{self.name}')"
        elif self.file:
            return f"Image(file='{self.file}')"
        else:
            return "Image(undefined)"

    def get_image_id(self) -> str:
        """Get a unique identifier for this image.

        In a real implementation, this would build or pull the image if needed.
        """
        if self.name:
            return self.name
        elif self.file:
            # In a real implementation, this would build the image from the Dockerfile
            # and return the image ID
            return f"local-build-{self.file.stem}"
        else:
            raise ValueError("Image must have either name or file defined")


class Secret(BaseModel):
    """Definition of a secret to be retrieved."""

    name: str
    key: str
    description: str = ""

    def __str__(self) -> str:
        return f"{self.name}/{self.key}"


# Global variable to hold the current pipeline instance
_PIPELINE = None


class Pipeline:
    """Pipeline configuration and execution."""

    def __init__(
        self,
        image: Image | None = None,
        dry_run: bool = False,
        param_overrides: dict | None = None,
    ):
        self.image = image
        self.dry_run = dry_run
        self._artifacts = {}
        self._secrets = {}
        self._branch = self._detect_branch()
        self._environment = getattr(self, "Environment", EnvironmentModel)()
        self.BuildParams = getattr(self, "BuildParams", BuildParamsModel)()
        self._triggers = getattr(self, "Triggers", TriggersModel)()

        # Apply parameter overrides if provided
        if param_overrides:
            for name, value in param_overrides.items():
                if hasattr(self.BuildParams, name):
                    setattr(self.BuildParams, name, value)
                    print(f"Parameter override: {name}={value}")
                else:
                    print(f"Warning: Unknown parameter '{name}' will be ignored")

    def get_secret(self, secret: str) -> str:
        """Get a secret from the pipeline's secret store.

        Parameters:
        :secret: The name of the secret as a string or Secret object
        """
        if isinstance(secret, Secret):
            key = f"{secret.name}/{secret.key}"
        else:
            key = secret

        if self.dry_run:
            print(f"[DRY RUN] Would retrieve secret: {key}")
            return f"[DRY_RUN_SECRET_{key}]"

        # In a real implementation, this would retrieve from a secure store
        # For now, we'll simulate with a placeholder
        if key not in self._secrets:
            # Simulate retrieved secret with a fake value
            self._secrets[key] = f"secret_{key}_value"

        return self._secrets[key]

    def _detect_branch(self) -> str:
        """Detect the current Git branch."""
        import os

        # In a real implementation, would detect from CI environment or Git
        return os.environ.get("GIT_BRANCH", "main")

    @property
    def branch(self) -> str:
        """Get the current Git branch name."""
        return self._branch

    def save_artifact(self, name: str, path: str):
        """Save a build artifact for later retrieval.

        Parameters:
        :name: Unique name for the artifact
        :path: Path to the artifact (can include glob patterns)
        """
        import glob
        from pathlib import Path

        if self.dry_run:
            print(f"[DRY RUN] Would save artifact '{name}' from path: {path}")
            self._artifacts[name] = []
            return

        resolved_paths = list(glob.glob(path))
        if not resolved_paths:
            print(f"Warning: No files found matching '{path}'")
            return

        self._artifacts[name] = [Path(p) for p in resolved_paths]
        print(f"Artifact '{name}' saved: {resolved_paths}")

    def __enter__(self):
        """Enter the pipeline context."""
        global _PIPELINE
        _PIPELINE = self
        print(f"Starting pipeline with {self.image}")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Exit the pipeline context."""
        global _PIPELINE
        exc_tb  # pyright: ignore[reportUnusedExpression]
        if exc_type is None:
            print("Pipeline completed successfully")
        elif exc_type is SystemExit:
            print("Pipeline exited early")
            return True  # Suppress the exception
        else:
            print(f"Pipeline failed: {exc_type}")
        _PIPELINE = None


@dataclass
class Result:
    """Result of a command execution.

    Contains the standard output, standard error, and return code of the command.
    """

    stdout: str
    stderr: str
    returncode: int

    def __bool__(self) -> bool:
        """Return True if the command succeeded (return code is 0)."""
        return self.returncode == 0

    def __str__(self) -> str:
        status = "success" if self.returncode == 0 else f"failed ({self.returncode})"
        return f"Result({status}, stdout={len(self.stdout)} chars, stderr={len(self.stderr)} chars)"


class Stage:
    """A stage in a pipeline execution."""

    def __init__(self, name: str, *, image: Image | None = None, timeout: int = 0):
        self.name = name
        self.image = image
        self.timeout = timeout
        self.start_time = None
        self.end_time = None

    def __enter__(self):
        """Enter the stage context."""
        import time

        self.start_time = time.monotonic()
        self.log(f"Starting stage '{self.name}'")
        if self.image:
            self.log(f"Using image: {self.image}")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Exit the stage context."""
        import time

        self.end_time = time.monotonic()
        assert self.start_time is not None
        duration = self.end_time - self.start_time

        if exc_type is None:
            exc_tb  # pyright: ignore[reportUnusedExpression]
            self.log(f"Stage '{self.name}' completed in {duration:.2f}s")
        elif exc_type is ExitStage:
            self.log(f"Stage '{self.name}' exited: {exc_val}")
            return True  # Suppress the exception
        else:
            self.log(f"Stage '{self.name}' failed after {duration:.2f}s: {exc_val}")

        if self.timeout > 0 and duration > self.timeout:
            self.log(f"Warning: Stage exceeded timeout of {self.timeout}s")

    def run(self, command: str) -> Result:
        """Run a command in the stage environment.

        Parameters:
        :command: The shell command to execute

        Returns:
        :Result: Object containing stdout, stderr and return code
        """
        import shlex
        import subprocess

        # Get the pipeline instance to check for dry run mode
        global _PIPELINE
        pipeline = _PIPELINE

        if pipeline and pipeline.dry_run:
            self.log(f"[DRY RUN] Would execute: {command}")
            # Return a successful result since we're not actually running the command
            return Result(
                stdout=f"[DRY RUN] Output from: {command}", stderr="", returncode=0
            )

        self.log(f"Running: {command}")

        try:
            # In a real implementation, this would use the specified image
            # For now, we'll just run locally
            process = subprocess.run(
                shlex.split(command),
                capture_output=True,
                text=True,
                check=False,
            )

            result = Result(
                stdout=process.stdout,
                stderr=process.stderr,
                returncode=process.returncode,
            )

            if result.returncode != 0:
                self.log(f"Command failed with exit code {result.returncode}")
                if result.stderr:
                    self.log(f"Error output: {result.stderr[:200]}...")
            else:
                self.log("Command completed successfully")

            return result
        except Exception as e:
            self.log(f"Error executing command: {e}")
            return Result(stdout="", stderr=str(e), returncode=1)

    def log(self, message: str):
        """Log a message to the console."""
        print(f"[Danube][{self.name}] {message}")

    def input(
        self,
        kind: str = "text",
        description: str = "",
        timeout: int = 300,
    ) -> str:
        """Request input from the user during pipeline execution.

        Parameters:
        :kind: kind of input ('text', 'confirm', etc.)
        :name: Name of the input parameter
        :description: Description to show the user
        :timeout: Timeout in seconds

        Returns:
        :str: The user input value
        """
        # Check if we're in dry run mode
        global _PIPELINE
        pipeline = _PIPELINE

        if pipeline and pipeline.dry_run:
            self.log(
                f"[DRY RUN] Would request input: {description} (timeout: {timeout}s)"
            )
            # Return default values for different input types
            if kind == "confirm":
                self.log("[DRY RUN] Assuming 'yes' for confirmation")
                return "True"
            else:
                self.log(f"[DRY RUN] Assuming default text input for: {description}")
                return f"[DRY_RUN_INPUT:{description}]"

        self.log(f"Requesting input: {description} ({timeout}s)")

        # In a real CI implementation, this would integrate with
        # a UI or API for receiving user input

        # For a local implementation, let's use Python's input function
        if kind == "confirm":
            response = input(f"{description} (y/n): ").lower()
            return str(response.startswith("y"))
        else:
            return input(f"{description}: ")


class Parameter:
    """Base class for pipeline parameters with validation."""

    def __init__(
        self, name: str, description: str, default: Any = None, required: bool = False
    ):
        self.name = name
        self.description = description
        self._value = default
        self.required = required
        self._validated = default is not None

        # Validate the default value if provided
        if default is not None:
            self.validate(default)

    @property
    def value(self) -> Any:
        """Get the parameter value, raising an error if required but not set."""
        if self.required and not self._validated:
            raise ValueError(f"Required parameter '{self.name}' has not been set")
        return self._value

    @value.setter
    def value(self, val: Any):
        """Set the parameter value, validating it first."""
        self.validate(val)
        self._value = val
        self._validated = True

    def validate(self, value: Any) -> None:
        """Validate a value for this parameter.

        Subclasses should override this to provide type-specific validation.
        """
        if value is None and self.required:
            raise ValueError(f"Required parameter '{self.name}' cannot be None")

    def __str__(self) -> str:
        """String representation of the parameter."""
        return f"{self.name}={self._value}"

    def __repr__(self) -> str:
        """Detailed representation of the parameter."""
        return f"{self.__class__.__name__}(name='{self.name}', value={self._value}, required={self.required})"


class StringParameter(Parameter):
    """Parameter that accepts string values with optional validation against choices."""

    def __init__(
        self,
        name: str,
        description: str,
        default: str | None = None,
        required: bool = False,
        choices: Optional[List[str]] = None,
    ):
        self.choices = choices
        super().__init__(name, description, default, required)

    def validate(self, value: Any) -> None:
        """Validate that the value is a string and matches allowed choices if specified."""
        super().validate(value)

        if value is None:
            return

        if not isinstance(value, str):
            raise ValueError(
                f"Parameter '{self.name}' must be a string, got {type(value).__name__}"
            )

        if self.choices and value not in self.choices:
            choices_str = ", ".join(f"'{c}'" for c in self.choices)
            raise ValueError(
                f"Parameter '{self.name}' must be one of: {choices_str}, got '{value}'"
            )


class IntParameter(Parameter):
    """Parameter that accepts integer values with optional min/max validation."""

    def __init__(
        self,
        name: str,
        description: str,
        default: int | None = None,
        required: bool = False,
        min_value: Optional[int] = None,
        max_value: Optional[int] = None,
    ):
        self.min_value = min_value
        self.max_value = max_value
        super().__init__(name, description, default, required)

    def validate(self, value: Any) -> None:
        """Validate that the value is an integer and within bounds if specified."""
        super().validate(value)

        if value is None:
            return

        if not isinstance(value, int):
            raise ValueError(
                f"Parameter '{self.name}' must be an integer, got {type(value).__name__}"
            )

        if self.min_value is not None and value < self.min_value:
            raise ValueError(
                f"Parameter '{self.name}' must be at least {self.min_value}, got {value}"
            )

        if self.max_value is not None and value > self.max_value:
            raise ValueError(
                f"Parameter '{self.name}' must be at most {self.max_value}, got {value}"
            )


class BoolParameter(Parameter):
    """Parameter that accepts boolean values."""

    def __init__(
        self,
        name: str,
        description: str,
        default: bool | None = None,
        required: bool = False,
    ):
        super().__init__(name, description, default, required)

    def validate(self, value: Any) -> None:
        """Validate that the value is a boolean."""
        super().validate(value)

        if value is None:
            return

        if not isinstance(value, bool):
            raise ValueError(
                f"Parameter '{self.name}' must be a boolean, got {type(value).__name__}"
            )


def secret(name: str, key: str, description: str = ""):
    """Define a secret to be retrieved from the secrets store.

    Parameters:
    :name: The name of the secret provider or context
    :key: The key of the specific secret
    :description: Optional description of what the secret is used for

    Returns:
    :Secret: A Secret object that can be passed to pipeline.get_secret()
    """
    return Secret(name=name, key=key, description=description)
