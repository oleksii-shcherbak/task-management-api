from datetime import datetime

from pydantic import BaseModel, Field

from app.models.project import ProjectStatus


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    category: str | None = Field(default=None, max_length=100)
    deadline: datetime | None = None


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    category: str | None = Field(default=None, max_length=100)
    status: ProjectStatus | None = None
    deadline: datetime | None = None


class ProjectResponse(BaseModel):
    id: int
    name: str
    description: str | None
    category: str | None
    status: ProjectStatus
    deadline: datetime | None
    owner_id: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
