from __future__ import annotations

from pydantic import BaseModel, Field

from app.models.task_status import StatusType


class StatusCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    color: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    type: StatusType


class StatusUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    color: str | None = Field(default=None, pattern=r"^#[0-9A-Fa-f]{6}$")
    position: int | None = Field(default=None, ge=1)
    is_default: bool | None = None
