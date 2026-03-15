from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.project import Project
    from app.models.task import Task
    from app.models.user import User


class ActivityLog(Base):
    __tablename__ = "activity_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    project_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Nullable - project-level actions have no task
    task_id: Mapped[int | None] = mapped_column(
        Integer,
        # SET NULL not CASCADE - if a task is deleted, keep project-level history
        ForeignKey("tasks.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Nullable - keep log entries even if the user account is deleted
    user_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # E.g. "created task", "updated project name", "deleted task"
    action: Mapped[str] = mapped_column(String(100), nullable=False)

    # Snapshot of the display value at the time of the event - not IDs,
    # so the log remains accurate even if names change later
    old_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_value: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    project: Mapped[Project] = relationship("Project", back_populates="activity_logs")
    task: Mapped[Task | None] = relationship("Task", back_populates="activity_logs")
    actor: Mapped[User | None] = relationship("User", back_populates="activity_logs")

    def __repr__(self) -> str:
        return f"<ActivityLog(id={self.id}, action='{self.action}', project_id={self.project_id})>"
