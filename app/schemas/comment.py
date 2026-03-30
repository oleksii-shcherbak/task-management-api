from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class CommentAuthor(BaseModel):
    id: int
    name: str

    model_config = ConfigDict(from_attributes=True)


class MentionedUser(BaseModel):
    id: int
    username: str
    full_name: str = Field(validation_alias="name")

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class CommentCreate(BaseModel):
    content: str = Field(..., min_length=1, max_length=10000)


class CommentUpdate(BaseModel):
    content: str = Field(..., min_length=1, max_length=10000)


class CommentResponse(BaseModel):
    id: int
    task_id: int
    author: CommentAuthor | None
    content: str
    mentions: list[MentionedUser] = []
    edited_at: datetime | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
