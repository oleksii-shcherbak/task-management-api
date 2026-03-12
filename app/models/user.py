from __future__ import annotations

from datetime import datetime
from enum import Enum as PyEnum
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, Enum, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.project import Project
    from app.models.project_member import ProjectMember
    from app.models.refresh_token import RefreshToken
    from app.models.task import Task


class UserRole(PyEnum):
    MEMBER = "member"
    MANAGER = "manager"
    ADMIN = "admin"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole),
        default=UserRole.MEMBER,
        server_default=UserRole.MEMBER.name,
        nullable=False,
    )
    # New users start inactive
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    # One user has many refresh tokens
    refresh_tokens: Mapped[list[RefreshToken]] = relationship(
        "RefreshToken",
        back_populates="user",
        cascade="all, delete-orphan",  # deleting User deletes their tokens in SQLAlchemy session
    )

    owned_projects: Mapped[list[Project]] = relationship(
        "Project",
        back_populates="owner",
        cascade="all, delete-orphan",
    )

    project_memberships: Mapped[list[ProjectMember]] = relationship(
        "ProjectMember",
        back_populates="user",
        cascade="all, delete-orphan",
    )

    assigned_tasks: Mapped[list[Task]] = relationship(
        "Task",
        back_populates="assignee",
    )

    def __repr__(self) -> str:
        return f"<User(id={self.id}, email='{self.email}')>"  # Simple string representation for debugging
