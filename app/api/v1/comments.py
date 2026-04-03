from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import delete, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_current_user, get_member_or_403, get_project_or_404
from app.core.arq_pool import get_arq_pool
from app.core.exceptions import ForbiddenError, NotFoundError, ValidationError
from app.database import get_db
from app.models.comment import Comment
from app.models.comment_mention import CommentMention
from app.models.project_member import ProjectRole
from app.models.task import Task
from app.models.user import User
from app.schemas.comment import CommentCreate, CommentResponse, CommentUpdate
from app.utils.mentions import parse_mentioned_usernames, resolve_mention_user_ids
from app.utils.pagination import CursorPage, decode_cursor, paginate_query

# Routes that need project + task context: /projects/{project_id}/tasks/{task_id}/comments
project_tasks_router = APIRouter(tags=["Comments"])

# Routes that operate on a single comment directly: /comments/{comment_id}
comments_router = APIRouter(tags=["Comments"])


async def get_comment_or_404(comment_id: int, db: AsyncSession) -> Comment:
    result = await db.execute(
        select(Comment)
        .where(Comment.id == comment_id)
        .options(
            selectinload(Comment.author),
            selectinload(Comment.mentions),
        )
    )
    comment: Comment | None = result.scalar_one_or_none()
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
    task: Task | None = result.scalar_one_or_none()
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
    arq_pool=Depends(get_arq_pool),
) -> Comment:
    await get_project_or_404(project_id, db)
    await get_member_or_403(project_id, current_user.id, db)
    await get_task_or_404(task_id, project_id, db)

    comment = Comment(
        task_id=task_id,
        user_id=current_user.id,
        content=body.content,
    )
    db.add(comment)
    await db.flush()

    mentioned_ids = await resolve_mention_user_ids(
        parse_mentioned_usernames(body.content),
        project_id,
        current_user.id,
        db,
    )
    for uid in mentioned_ids:
        db.add(
            CommentMention(comment_id=comment.id, user_id=uid, actor_id=current_user.id)
        )

    await db.commit()

    for uid in mentioned_ids:
        await arq_pool.enqueue_job(
            "send_mention_notification",
            user_id=uid,
            actor_name=current_user.name,
            source_type="comment",
            source_id=comment.id,
            body_excerpt=body.content[:200],
        )

    return await get_comment_or_404(comment.id, db)


@project_tasks_router.get(
    "/{project_id}/tasks/{task_id}/comments",
    response_model=CursorPage[CommentResponse],
)
async def list_comments(
    project_id: int,
    task_id: int,
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CursorPage[CommentResponse]:
    await get_project_or_404(project_id, db)
    await get_member_or_403(project_id, current_user.id, db)
    await get_task_or_404(task_id, project_id, db)

    cursor_data: dict | None = None
    if cursor is not None:
        cursor_data = decode_cursor(cursor)
        if cursor_data is None:
            raise ValidationError("Invalid cursor")

    query = (
        select(Comment)
        .where(Comment.task_id == task_id)
        .options(selectinload(Comment.author), selectinload(Comment.mentions))
    )

    if cursor_data is not None:
        cursor_created_at = datetime.fromisoformat(cursor_data["created_at"])
        query = query.where(
            tuple_(Comment.created_at, Comment.id)
            > (cursor_created_at, cursor_data["id"])
        )

    query = query.order_by(Comment.created_at.asc(), Comment.id.asc())

    return await paginate_query(
        db,
        query,
        limit,
        lambda c: {"created_at": c.created_at.isoformat(), "id": c.id},
    )


@comments_router.patch("/{comment_id}", response_model=CommentResponse)
async def edit_comment(
    comment_id: int,
    body: CommentUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    arq_pool=Depends(get_arq_pool),
) -> Comment:
    comment = await get_comment_or_404(comment_id, db)

    if comment.user_id != current_user.id:
        raise ForbiddenError("You can only edit your own comments")

    task_result = await db.execute(select(Task).where(Task.id == comment.task_id))
    task = task_result.scalar_one()

    existing_ids = {m.id for m in comment.mentions}
    new_ids = await resolve_mention_user_ids(
        parse_mentioned_usernames(body.content),
        task.project_id,
        current_user.id,
        db,
    )

    removed_ids = existing_ids - new_ids
    added_ids = new_ids - existing_ids

    if removed_ids:
        await db.execute(
            delete(CommentMention).where(
                CommentMention.comment_id == comment_id,
                CommentMention.user_id.in_(removed_ids),
            )
        )
    for uid in added_ids:
        db.add(
            CommentMention(comment_id=comment_id, user_id=uid, actor_id=current_user.id)
        )

    comment.content = body.content
    comment.edited_at = datetime.now(UTC)
    await db.commit()
    db.expunge(comment)

    for uid in added_ids:
        await arq_pool.enqueue_job(
            "send_mention_notification",
            user_id=uid,
            actor_name=current_user.name,
            source_type="comment",
            source_id=comment_id,
            body_excerpt=body.content[:200],
        )

    return await get_comment_or_404(comment.id, db)


@comments_router.delete("/{comment_id}", status_code=204)
async def delete_comment(
    comment_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
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
