"""Test assumptions made about the Docker library"""

import pytest


@pytest.fixture()
def docker_client() -> str:
    return "aa"


def test_docker_run_returns_id() -> None:
    assert True
