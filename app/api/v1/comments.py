from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_current_user, get_member_or_403, get_project_or_404
from app.core.exceptions import ForbiddenError, NotFoundError
from app.database import get_db
from app.models.comment import Comment
from app.models.project_member import ProjectRole
from app.models.task import Task
from app.models.user import User
from app.schemas.comment import CommentCreate, CommentResponse, CommentUpdate

# Routes that need project + task context: /projects/{project_id}/tasks/{task_id}/comments
project_tasks_router = APIRouter()

# Routes that operate on a single comment directly: /comments/{comment_id}
comments_router = APIRouter()


async def get_comment_or_404(comment_id: int, db: AsyncSession) -> Comment:
    result = await db.execute(
        select(Comment)
        .where(Comment.id == comment_id)
        .options(selectinload(Comment.author))
    )
    comment = result.scalar_one_or_none()
    if comment is None:
        raise NotFoundError("Comment not found")
    return comment


async def get_task_or_404(task_id: int, project_id: int, db: AsyncSession) -> Task:
    result = await db.execute(
        select(Task).where(
            Task.id == task_id,
            Task.project_id == project_id,
            Task.deleted_at.is_(None),
        )
    )
    task = result.scalar_one_or_none()
    if task is None:
        raise NotFoundError("Task not found")
    return task


@project_tasks_router.post(
    "/{project_id}/tasks/{task_id}/comments",
    response_model=CommentResponse,
    status_code=201,
)
async def add_comment(
    project_id: int,
    task_id: int,
    body: CommentCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Verify project exists and user is a member
    await get_project_or_404(project_id, db)
    await get_member_or_403(project_id, current_user.id, db)

    # Verify task belongs to this project and isn't deleted
    await get_task_or_404(task_id, project_id, db)

    comment = Comment(
        task_id=task_id,
        user_id=current_user.id,
        content=body.content,
    )
    db.add(comment)
    await db.commit()

    # Re-fetch with author loaded - can't access relationships after commit without this
    return await get_comment_or_404(comment.id, db)


@project_tasks_router.get(
    "/{project_id}/tasks/{task_id}/comments",
    response_model=list[CommentResponse],
)
async def list_comments(
    project_id: int,
    task_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await get_project_or_404(project_id, db)
    await get_member_or_403(project_id, current_user.id, db)
    await get_task_or_404(task_id, project_id, db)

    result = await db.execute(
        select(Comment)
        .where(Comment.task_id == task_id)
        .options(selectinload(Comment.author))
        .order_by(Comment.created_at.asc())
    )
    return result.scalars().all()


@comments_router.patch("/{comment_id}", response_model=CommentResponse)
async def edit_comment(
    comment_id: int,
    body: CommentUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    comment = await get_comment_or_404(comment_id, db)

    # Only the author can edit their own comment
    if comment.user_id != current_user.id:
        raise ForbiddenError("You can only edit your own comments")

    comment.content = body.content
    comment.edited_at = datetime.now(UTC)
    await db.commit()

    return await get_comment_or_404(comment.id, db)


@comments_router.delete("/{comment_id}", status_code=204)
async def delete_comment(
    comment_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    comment = await get_comment_or_404(comment_id, db)

    # To check project role we need the task's project_id
    task_result = await db.execute(select(Task).where(Task.id == comment.task_id))
    task = task_result.scalar_one()

    current_member = await get_member_or_403(task.project_id, current_user.id, db)

    # Owner/manager can delete any comment. Members can only delete their own
    if current_member.role not in (ProjectRole.OWNER, ProjectRole.MANAGER):
        if comment.user_id != current_user.id:
            raise ForbiddenError("You can only delete your own comments")

    await db.delete(comment)
    await db.commit()
