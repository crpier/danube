import pytest
from sqlalchemy import delete
from sqlalchemy.orm import sessionmaker

from src.depends import Session
from src.model import Base, User, get_engine
from src.schema import EmailAddr, UserCreate
from src.user_service import AuthenticationError, authenticate_user, create_user


@pytest.fixture()
def base_user() -> UserCreate:
    return UserCreate(
        username="test_username",
        email=EmailAddr("test@example.com"),
        password="test_password",  # ,
    )


@pytest.fixture(scope="module")
def session() -> Session:
    engine = get_engine("sqlite+pysqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(engine)


@pytest.fixture()
def _cleanup_table(session: Session) -> None:
    with session() as s:
        s.execute(delete(User))
        s.commit()


@pytest.mark.component()
@pytest.mark.usefixtures("_cleanup_table")
def test_password_is_hashed(session: Session, base_user: UserCreate) -> None:
    new_user_id = create_user(session, base_user)
    with session() as s:
        created_user = s.get(User, new_user_id)
        assert created_user, "User was not created"
        assert created_user.pass_hash
        assert created_user.pass_hash != base_user.password


@pytest.mark.component()
@pytest.mark.usefixtures("_cleanup_table")
def test_authentication(session: Session, base_user: UserCreate) -> None:
    _ = create_user(session, base_user)
    authenticated_user = authenticate_user(
        session=session,
        username=base_user.username,
        password=base_user.password,
    )
    assert authenticated_user.username == base_user.username


@pytest.mark.component()
@pytest.mark.usefixtures("_cleanup_table")
def test_authentication_wrong_username(session: Session, base_user: UserCreate) -> None:
    _ = create_user(session, base_user)
    with pytest.raises(AuthenticationError):
        authenticate_user(
            session=session,
            username="does_not_exist",
            password=base_user.password,
        )
