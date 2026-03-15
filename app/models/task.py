from __future__ import annotations

from datetime import datetime
from enum import Enum as PyEnum
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.activity_log import ActivityLog
    from app.models.comment import Comment
    from app.models.project import Project
    from app.models.task_status import TaskStatus
    from app.models.user import User


class TaskPriority(PyEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    URGENT = "urgent"


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    project_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # RESTRICT: don't allow deleting a status if tasks are still assigned to it,
    # to prevent orphaned tasks with broken FK references.
    status_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("task_statuses.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    # SET NULL: if an assignee is deleted, keep the task but just mark it unassigned.
    assignee_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )

    title: Mapped[str] = mapped_column(String(255), nullable=False)

    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    priority: Mapped[TaskPriority | None] = mapped_column(
        Enum(TaskPriority, name="taskpriority"), nullable=True
    )

    # Used for ordering tasks within a status column. New tasks get position = max + 1.
    position: Mapped[int] = mapped_column(Integer, nullable=False)

    due_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
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

    project: Mapped[Project] = relationship("Project", back_populates="tasks")
    status: Mapped[TaskStatus] = relationship("TaskStatus", back_populates="tasks")
    assignee: Mapped[User | None] = relationship(
        "User", back_populates="assigned_tasks"
    )
    comments: Mapped[list[Comment]] = relationship(
        "Comment", back_populates="task", cascade="all, delete-orphan"
    )
    activity_logs: Mapped[list[ActivityLog]] = relationship(
        "ActivityLog", back_populates="task"
    )

    def __repr__(self) -> str:
        return f"<Task(id={self.id}, title='{self.title}')>"
