from pathlib import Path
from unittest.mock import MagicMock

import pytest

from danube.runner import DanubeAPI, DanubeRunner
from lib.danube import Pipeline, Stage, pipeline


@pytest.fixture
def mock_api():
    """
    Fixture to create a mock DanubeAPI for testing.
    This allows us to isolate the tests from the actual API calls.
    """
    return MagicMock(spec=DanubeAPI)


@pytest.fixture
def mock_runner():
    """
    Fixture to create a mock DanubeRunner for testing.
    This allows us to isolate the tests from the actual Docker operations.
    """
    return MagicMock(spec=DanubeRunner)


def test_pipeline_decorator():
    """
    Test the pipeline decorator to ensure it properly wraps a function
    and creates a Pipeline instance.
    """
    mock_api = MagicMock(spec=DanubeAPI)

    @pipeline(image="test_image:latest")
    def test_pipeline(p: Pipeline):
        assert isinstance(p, Pipeline)
        assert p.image == "test_image:latest"

    test_pipeline(mock_api)
    mock_api.is_default_branch.assert_called_once()


def test_pipeline_with_dockerfile():
    """
    Test the pipeline decorator with a Dockerfile path instead of an image name.
    This ensures the Pipeline can handle both string image names and Path objects.
    """
    mock_api = MagicMock(spec=DanubeAPI)
    dockerfile_path = Path("./Dockerfile")

    @pipeline(image=dockerfile_path)
    def test_pipeline(p: Pipeline):
        assert isinstance(p, Pipeline)
        assert p.image == dockerfile_path

    test_pipeline(mock_api)
    mock_api.build_image.assert_called_once_with(dockerfile_path)


def test_stage_context_manager(mock_api):
    """
    Test the Stage context manager to ensure it properly sets and unsets
    the current stage in the API.
    """
    with Stage("test_stage", api=mock_api) as stage:
        assert isinstance(stage, Stage)
        mock_api.set_current_stage.assert_called_with("test_stage")
        mock_api.log.assert_called_with("Starting stage: test_stage")

    mock_api.set_current_stage.assert_called_with(None)
    mock_api.log.assert_called_with("Finished stage: test_stage")


def test_stage_run_command(mock_api):
    """
    Test the Stage.run method with a string command to ensure it
    properly delegates to the API's run_command method.
    """
    stage = Stage("test_stage", api=mock_api)
    stage.run("echo 'Hello, World!'")

    mock_api.run_command.assert_called_once()
    mock_api.get_logs.assert_called_once()


def test_stage_run_callable(mock_api):
    """
    Test the Stage.run method with a callable to ensure it
    properly executes the function and logs the result.
    """

    def test_function():
        return "Function result"

    stage = Stage("test_stage", api=mock_api)
    stage.run(test_function)

    mock_api.log.assert_any_call("Running Python function: test_function")
    mock_api.log.assert_any_call("Function result: Function result")


@pytest.mark.integration
def test_pipeline_integration(mock_runner):
    """
    Integration test to ensure the pipeline runs correctly with multiple stages.
    This test simulates a simple pipeline with linting and testing stages.
    """
    mock_api = MagicMock(spec=DanubeAPI)
    mock_runner.api = mock_api

    @pipeline(image="python:3.9")
    def test_pipeline(p: Pipeline):
        with Stage("linting") as s:
            s.run("flake8 .")
        with Stage("testing") as s:
            s.run("pytest")

    test_pipeline(mock_api)

    assert mock_api.set_current_stage.call_count == 4  # 2 enters, 2 exits
    assert mock_api.run_command.call_count == 2
    assert mock_api.get_logs.call_count == 2


@pytest.mark.integration
def test_pipeline_with_conditional_stage(mock_runner):
    """
    Integration test to ensure the pipeline correctly handles conditional stages
    based on the default branch status.
    """
    mock_api = MagicMock(spec=DanubeAPI)
    mock_runner.api = mock_api
    mock_api.is_default_branch.return_value = True

    @pipeline(image="python:3.9")
    def test_pipeline(p: Pipeline):
        with Stage("always_run") as s:
            s.run("echo 'This always runs'")
        if p.on_default_branch:
            with Stage("deploy") as s:
                s.run("echo 'Deploying...'")

    test_pipeline(mock_api)

    assert mock_api.run_command.call_count == 2
    mock_api.run_command.assert_any_call(
        "python:3.9", "echo 'This always runs'", mock.ANY
    )
    mock_api.run_command.assert_any_call("python:3.9", "echo 'Deploying...'", mock.ANY)


@pytest.mark.integration
def test_pipeline_with_error_handling(mock_runner):
    """
    Integration test to ensure the pipeline correctly handles and logs errors
    that occur during stage execution.
    """
    mock_api = MagicMock(spec=DanubeAPI)
    mock_runner.api = mock_api
    mock_api.run_command.side_effect = [None, Exception("Command failed")]

    @pipeline(image="python:3.9")
    def test_pipeline(p: Pipeline):
        with Stage("successful_stage") as s:
            s.run("echo 'This succeeds'")
        with Stage("failing_stage") as s:
            s.run("echo 'This fails'")

    with pytest.raises(Exception):
        test_pipeline(mock_api)

    mock_api.log.assert_any_call("Stage failing_stage failed: Command failed")


if __name__ == "__main__":
    pytest.main()
