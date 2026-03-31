from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.schemas.auth import RESERVED_USERNAMES, USERNAME_RE


class UserResponse(BaseModel):
    id: int
    name: str
    username: str
    email: str
    role: str
    is_active: bool
    is_verified: bool
    avatar_url: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class PublicUserResponse(BaseModel):
    id: int
    name: str
    username: str
    avatar_url: str | None

    model_config = {"from_attributes": True}


class UserUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    email: EmailStr | None = None
    username: str | None = None

    @field_validator("username")
    @classmethod
    def validate_username(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if not USERNAME_RE.match(v):
            raise ValueError(
                "Username must be 3-30 characters: lowercase letters, digits, underscores, hyphens"
            )
        if v in RESERVED_USERNAMES:
            raise ValueError(f"'{v}' is a reserved username")
        return v


class PasswordChange(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8)


class MentionInboxItem(BaseModel):
    source_type: str
    task_id: int
    project_id: int
    project_name: str
    actor_name: str
    actor_username: str
    body_excerpt: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
