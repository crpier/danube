from collections.abc import Generator

import pytest
from pydantic import EmailStr
from sqlalchemy.orm import Session

from danube.model import Base, User
from danube.schema import UserCreate
from danube.user_service import AuthenticationError, authenticate_user, create_user
from tests.conftest import get_test_session


@pytest.fixture(autouse=True)
def test_session() -> Generator[None, None, None]:
    session = get_test_session()
    with session() as s:
        Base.metadata.create_all(s.get_bind())
        yield s
        Base.metadata.drop_all(s.get_bind())


@pytest.fixture()
def base_user() -> UserCreate:
    return UserCreate(
        username="test_username",
        email=EmailStr("test@example.com"),
        password="test_password",  # ,
    )


@pytest.mark.component()
def test_password_is_hashed(test_session: Session, base_user: UserCreate) -> None:
    new_user_id = create_user(base_user)
    created_user = test_session.get(User, new_user_id)
    assert created_user, "User was not created"
    assert created_user.pass_hash
    assert created_user.pass_hash != base_user.password


@pytest.mark.component()
def test_authentication(test_session: Session, base_user: UserCreate) -> None:
    _ = create_user(base_user)
    authenticated_user = authenticate_user(
        session=test_session,
        username=base_user.username,
        password=base_user.password,
    )
    assert authenticated_user.username == base_user.username


@pytest.mark.component()
def test_authentication_wrong_username(
    test_session: Session,
    base_user: UserCreate,
) -> None:
    _ = create_user(base_user)
    with pytest.raises(AuthenticationError):
        authenticate_user(
            session=test_session,
            username="does_not_exist",
            password=base_user.password,
        )
