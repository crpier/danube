from sqlalchemy import select

from danube.depends import Session
from danube.model import User
from danube.schema import UserCreate, UserId, UserView
from danube.security import get_password_hash, verify_password


class AuthenticationError(Exception):
    ...


def get_users(session: Session) -> list[UserView]:
    with session() as s:
        res = s.execute(select(User)).all()
        return [UserView.from_orm(user[0]) for user in res]


def create_user(session: Session, user_create: UserCreate) -> UserId:
    with session() as s:
        # TODO: can we make this implicit and type safe?
        new_user = User(
            username=user_create.username,
            email=user_create.email,
            pass_hash=get_password_hash(user_create.password),
        )
        s.add(new_user)
        s.commit()
        return UserId(new_user.id)


def authenticate_user(session: Session, username: str, password: str) -> UserView:
    """Authenticate with username and password

    Returns:
        A client-facing model of the User

    Raises:
        AuthenticationError: if the username does not exist or
            the password is incorrect
    """
    with session() as s:
        res = s.execute(select(User).where(User.username == username)).one_or_none()
        if not res:
            raise AuthenticationError
        user: User = res[0]
        if not verify_password(password, user.pass_hash):
            raise AuthenticationError
        return UserView.from_orm(user)
