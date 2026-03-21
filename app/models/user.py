from __future__ import annotations

from datetime import datetime
from enum import Enum as PyEnum
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, Enum, Index, Integer, String, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.activity_log import ActivityLog
    from app.models.comment import Comment
    from app.models.email_verification_token import EmailVerificationToken
    from app.models.oauth_account import OAuthAccount
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
    __table_args__ = (
        # Partial unique index: allows re-registration after soft delete
        Index(
            "ix_users_email_active",
            "email",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    # None for OAuth-only users who have no password
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
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
    is_verified: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    # Updated on password change - used to invalidate tokens issued before the change
    password_changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    avatar_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Internal storage path (e.g. "avatars/uuid.jpg") - used to delete the old file on replacement.
    # Kept separate from avatar_url so the URL format can vary by backend.
    avatar_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
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
        secondary="task_assignees",
        primaryjoin="User.id == TaskAssignee.user_id",
        secondaryjoin="TaskAssignee.task_id == Task.id",
        viewonly=True,
    )

    comments: Mapped[list[Comment]] = relationship("Comment", back_populates="author")

    activity_logs: Mapped[list[ActivityLog]] = relationship(
        "ActivityLog", back_populates="actor"
    )

    oauth_accounts: Mapped[list[OAuthAccount]] = relationship(
        "OAuthAccount", back_populates="user", cascade="all, delete-orphan"
    )

    email_verification_tokens: Mapped[list[EmailVerificationToken]] = relationship(
        "EmailVerificationToken", back_populates="user", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<User(id={self.id}, email='{self.email}')>"  # Simple string representation for debugging
