from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.models.task import TaskPriority
from app.models.task_status import StatusType


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
    priority: TaskPriority | None
    position: int
    due_date: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class TaskReorder(BaseModel):
    status_id: int = Field(gt=0)
    position: int = Field(ge=1)
