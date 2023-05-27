import datetime

import jose
import jose.jwt
from passlib.hash import pbkdf2_sha256

from src.depends import Config


def create_access_token(data: dict, config: Config) -> str:
    to_encode = data.copy()
    expire = datetime.datetime.now(tz=datetime.timezone.utc) + datetime.timedelta(
        minutes=config.TOKEN_EXPIRE_MINUTES,
    )
    to_encode.update({"exp": expire})
    return jose.jwt.encode(
        to_encode,
        config.SECRET_KEY,
        algorithm=config.HASH_ALGORITHM,
    )


def get_password_hash(password: str) -> str:
    return pbkdf2_sha256.hash(password)


def verify_password(password: str, hashed_pass: str) -> bool:
    return pbkdf2_sha256.verify(password, hashed_pass)
