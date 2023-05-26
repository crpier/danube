import sqlalchemy.engine
from sqlalchemy.orm import DeclarativeBase


def get_engine(uri: str = "sqlite+pysqlite:///dev.db"):
    return sqlalchemy.engine.create_engine(uri)


class Base(DeclarativeBase):
    ...


class User(Base):
    __tablename__ = "users"
