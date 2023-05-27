from typing import NewType

from pydantic import BaseModel

EmailAddr = NewType("EmailAddr", str)
UserId = NewType("UserId", int)
JobId = NewType("JobId", int)


class UserCreate(BaseModel):
    username: str
    email: EmailAddr
    password: str

    class Config:
        orm_mode = True


class UserView(BaseModel):
    id: UserId
    username: str
    email: EmailAddr

    class Config:
        orm_mode = True
