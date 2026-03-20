from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.task import Task
    from app.models.user import User


class Attachment(Base):
    __tablename__ = "attachments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    task_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # SET NULL so the file record (and the actual file) survive user deletion.
    # The uploader is audit info, not a required relationship.
    uploader_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Original filename shown in the UI (e.g. "requirements.pdf"). Not guaranteed to be unique.
    filename: Mapped[str] = mapped_column(String(255), nullable=False)

    # Internal path within the storage backend (e.g. "attachments/uuid.pdf").
    # Never exposed directly in API responses - always converted via get_url().
    storage_path: Mapped[str] = mapped_column(String(512), nullable=False, unique=True)

    mime_type: Mapped[str] = mapped_column(String(127), nullable=False)

    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    task: Mapped[Task] = relationship("Task", back_populates="attachments")
    uploader: Mapped[User | None] = relationship("User")
