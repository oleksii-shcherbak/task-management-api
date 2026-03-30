from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Integer, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.comment_mention import CommentMention
    from app.models.task import Task
    from app.models.user import User


class Comment(Base):
    __tablename__ = "comments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    task_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Allow NULL for user_id to support comments from deleted users. In that case,
    # the comment will be attributed to "Deleted User" in the UI.
    user_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    content: Mapped[str] = mapped_column(Text, nullable=False)

    # Allow NULL for edited_at to indicate that the comment has never been edited.
    edited_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    task: Mapped[Task] = relationship("Task", back_populates="comments")
    author: Mapped[User | None] = relationship("User", back_populates="comments")
    mention_records: Mapped[list[CommentMention]] = relationship(
        "CommentMention", cascade="all, delete-orphan"
    )
    mentions: Mapped[list[User]] = relationship(
        "User",
        secondary="comment_mentions",
        primaryjoin="Comment.id == CommentMention.comment_id",
        secondaryjoin="CommentMention.user_id == User.id",
        viewonly=True,
    )

    def __repr__(self) -> str:
        return (
            f"<Comment(id={self.id}, task_id={self.task_id}, user_id={self.user_id})>"
        )
