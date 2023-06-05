from collections.abc import Generator

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from danube.entrypoints.rest import app
from danube.model import Base
from tests.conftest import get_test_session

pytestmark = pytest.mark.acceptance


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture(autouse=True)
def test_session() -> Generator[None, None, None]:
    session = get_test_session()
    with session() as s:
        Base.metadata.create_all(s.get_bind())
        yield s
        Base.metadata.drop_all(s.get_bind())


def test_get_pipeline_no_pipeline(client: TestClient) -> None:
    response = client.get("/api/v1/pipelines")
    assert response.status_code == status.HTTP_200_OK
    assert response.json() == []


def test_create_pipeline_invalid_body(client: TestClient) -> None:
    response = client.post("/api/v1/pipelines", json={})
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert response.json() == {
        "detail": [
            {
                "loc": ["body", "name"],
                "msg": "field required",
                "type": "value_error.missing",
            },
            {
                "loc": ["body", "source_repo"],
                "msg": "field required",
                "type": "value_error.missing",
            },
        ],
    }


def test_create_pipeline(client: TestClient, test_session: Session) -> None:
    body = {"name": "test", "source_repo": "https://github.com/crpier/danube.git"}
    response = client.post("/api/v1/pipelines", json=body)
    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {
        "status": "success",
        "message": "Pipeline test created with id 1",
    }
    res = test_session.execute(select(Base.metadata.tables["pipelines"])).all()
    assert res == [(1, "test", "https://github.com/crpier/danube.git", "danube.py")]
