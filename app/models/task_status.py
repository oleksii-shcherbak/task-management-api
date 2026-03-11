from __future__ import annotations

from datetime import datetime
from enum import Enum as PyEnum
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.project import Project
    from app.models.task import Task


class StatusType(PyEnum):
    UNSTARTED = "unstarted"
    STARTED = "started"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class TaskStatus(Base):
    __tablename__ = "task_statuses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    project_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    name: Mapped[str] = mapped_column(String(100), nullable=False)

    color: Mapped[str] = mapped_column(String(7), nullable=False)

    position: Mapped[int] = mapped_column(Integer, nullable=False)

    # Signals semantic meaning to the system - the display name is freeform,
    # but 'type' lets business logic ask "is this task done?" without string matching.
    type: Mapped[StatusType] = mapped_column(
        Enum(StatusType, name="statustype"), nullable=False
    )

    is_default: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    project: Mapped[Project] = relationship("Project", back_populates="task_statuses")
    tasks: Mapped[list[Task]] = relationship("Task", back_populates="status")

    __table_args__ = (
        Index(
            "ix_task_statuses_project_name_lower",
            "project_id",
            text("lower(name)"),
            unique=True,
        ),
        # Enforce that only one status per project can have is_default=True,
        # so we always have a well-known default status to fall back to when creating new tasks.
        Index(
            "ix_task_statuses_one_default_per_project",
            "project_id",
            unique=True,
            postgresql_where=text("is_default IS TRUE"),
        ),
    )

    def __repr__(self) -> str:
        return f"<TaskStatus(id={self.id}, name='{self.name}', project_id={self.project_id})>"
