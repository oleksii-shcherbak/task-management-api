from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.models.task import TaskPriority
from app.models.task_status import StatusType
from app.schemas.comment import MentionedUser


class TaskStatusResponse(BaseModel):
    id: int
    name: str
    color: str
    type: StatusType
    position: int
    is_default: bool

    model_config = {"from_attributes": True}


class AssigneeResponse(BaseModel):
    id: int
    name: str
    email: str

    model_config = {"from_attributes": True}


class TaskCreate(BaseModel):
    model_config = {
        "json_schema_extra": {
            "example": {
                "title": "Implement login page",
                "description": "Build the login form with email and password fields. Mention @alice for review.",
                "status_id": 1,
                "assignee_ids": [2, 3],
                "priority": "high",
                "due_date": "2026-05-01T00:00:00Z",
            }
        }
    }

    title: str = Field(min_length=1, max_length=255)
    description: str | None = None
    status_id: int | None = None
    assignee_ids: list[int] = Field(default_factory=list)
    priority: TaskPriority | None = None
    due_date: datetime | None = None


class TaskUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    status_id: int | None = Field(default=None, gt=0)
    assignee_ids: list[int] | None = None
    priority: TaskPriority | None = None
    due_date: datetime | None = None


class TaskResponse(BaseModel):
    id: int
    project_id: int
    title: str
    description: str | None
    status: TaskStatusResponse
    assignees: list[AssigneeResponse]
    mentions: list[MentionedUser] = []
    priority: TaskPriority | None
    position: int
    due_date: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class TaskReorder(BaseModel):
    status_id: int = Field(gt=0)
    position: int = Field(ge=1)
