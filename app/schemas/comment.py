from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class CommentAuthor(BaseModel):
    id: int
    name: str

    model_config = ConfigDict(from_attributes=True)


class CommentCreate(BaseModel):
    content: str = Field(..., min_length=1, max_length=10000)


class CommentUpdate(BaseModel):
    content: str = Field(..., min_length=1, max_length=10000)


class CommentResponse(BaseModel):
    id: int
    task_id: int
    author: CommentAuthor | None
    content: str
    edited_at: datetime | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
