import re

from pydantic import BaseModel, EmailStr, Field, field_validator

USERNAME_RE = re.compile(r"^[a-z0-9_-]{3,30}$")
RESERVED_USERNAMES = frozenset(
    {"me", "admin", "api", "null", "root", "support", "system", "anonymous"}
)


class RegisterRequest(BaseModel):
    model_config = {
        "json_schema_extra": {
            "example": {
                "email": "alice@example.com",
                "password": "securepassword123",
                "name": "Alice",
                "username": "alice",
            }
        }
    }

    email: EmailStr
    password: str = Field(min_length=8)
    name: str = Field(min_length=1, max_length=255)
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


class LoginRequest(BaseModel):
    model_config = {
        "json_schema_extra": {
            "example": {
                "identifier": "alice@example.com",
                "password": "securepassword123",
            }
        }
    }

    identifier: str
    password: str


class RefreshRequest(BaseModel):
    refresh_token: str


class TokenResponse(BaseModel):
    model_config = {
        "json_schema_extra": {
            "example": {
                "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
                "refresh_token": "dGhpcyBpcyBhIHJlZnJlc2ggdG9rZW4...",
                "token_type": "bearer",
            }
        }
    }

    access_token: str
    refresh_token: str
    token_type: str = "bearer"  # noqa: S105


class SetPasswordRequest(BaseModel):
    password: str = Field(min_length=8)


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str
    password: str = Field(min_length=8)
