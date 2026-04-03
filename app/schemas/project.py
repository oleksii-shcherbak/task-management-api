from datetime import datetime

from pydantic import BaseModel, Field

from app.models.project import ProjectStatus
from app.models.project_member import ProjectRole


class ProjectCreate(BaseModel):
    model_config = {
        "json_schema_extra": {
            "example": {
                "name": "Website Redesign",
                "description": "Redesign the company marketing site",
                "category": "Design",
                "deadline": "2026-12-31T00:00:00Z",
            }
        }
    }

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


class MemberAddRequest(BaseModel):
    user_id: int
    role: ProjectRole = ProjectRole.MEMBER


class MemberResponse(BaseModel):
    user_id: int
    project_id: int
    role: ProjectRole
    joined_at: datetime

    model_config = {"from_attributes": True}


class MemberRoleUpdate(BaseModel):
    role: ProjectRole


class MemberSearchResult(BaseModel):
    user_id: int
    username: str
    full_name: str
    avatar_url: str | None
