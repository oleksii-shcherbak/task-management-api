from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import asc, delete, func, select, tuple_, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_current_user, get_member_or_403, get_project_or_404
from app.core.arq_pool import get_arq_pool
from app.core.exceptions import ForbiddenError, NotFoundError, ValidationError
from app.database import get_db
from app.models.activity_log import ActivityLog
from app.models.project import Project
from app.models.project_member import ProjectMember, ProjectRole
from app.models.task import Task, TaskPriority
from app.models.task_assignee import TaskAssignee
from app.models.task_mention import TaskMention
from app.models.task_status import TaskStatus
from app.models.user import User
from app.schemas.activity_log import ActivityLogResponse
from app.schemas.task import TaskCreate, TaskReorder, TaskResponse, TaskUpdate
from app.services import task_service
from app.utils.mentions import parse_mentioned_usernames, resolve_mention_user_ids
from app.utils.pagination import CursorPage, decode_cursor, paginate_query

project_tasks_router = APIRouter(prefix="/projects", tags=["Tasks"])
tasks_router = APIRouter(prefix="/tasks", tags=["Tasks"])


async def get_task_or_404(task_id: int, db: AsyncSession) -> Task:
    result = await db.execute(
        select(Task)
        .options(
            selectinload(Task.status),
            selectinload(Task.assignees),
            selectinload(Task.task_assignees).selectinload(TaskAssignee.user),
            selectinload(Task.mentions),
            selectinload(Task.mention_records),
        )
        .where(
            Task.id == task_id,
            Task.deleted_at.is_(None),
        )
    )
    task: Task | None = result.scalar_one_or_none()
    if task is None:
        raise NotFoundError("Task not found")
    return task


async def get_next_position(project_id: int, status_id: int, db: AsyncSession) -> int:
    """Return MAX(position) + 1 within a status column, or 1 if the column is empty."""
    result = await db.execute(
        select(func.max(Task.position)).where(
            Task.project_id == project_id,
            Task.status_id == status_id,
            Task.deleted_at.is_(None),
        )
    )
    max_pos = result.scalar()
    return (max_pos or 0) + 1


# --- Create task ---


@project_tasks_router.post(
    "/{project_id}/tasks",
    status_code=status.HTTP_201_CREATED,
    response_model=TaskResponse,
)
async def create_task(
    project_id: int,
    body: TaskCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    arq_pool=Depends(get_arq_pool),
) -> Task:
    project = await get_project_or_404(project_id, db)

    member = await get_member_or_403(project_id, current_user.id, db)
    if member.role not in (ProjectRole.OWNER, ProjectRole.MANAGER):
        raise ForbiddenError("Only owners and managers can create tasks")

    # Resolve status: use provided status_id or fall back to the project's default
    if body.status_id is not None:
        result = await db.execute(
            select(TaskStatus).where(
                TaskStatus.id == body.status_id,
                TaskStatus.project_id == project_id,
            )
        )
        task_status: TaskStatus | None = result.scalar_one_or_none()
        if task_status is None:
            raise NotFoundError("Status not found in this project")
        status_id = task_status.id
    else:
        result = await db.execute(
            select(TaskStatus).where(
                TaskStatus.project_id == project_id,
                TaskStatus.is_default.is_(True),
            )
        )
        task_status: TaskStatus | None = result.scalar_one_or_none()
        if task_status is None:
            raise NotFoundError("No default status found for this project")
        status_id = task_status.id

    if body.assignee_ids:
        result = await db.execute(
            select(ProjectMember.user_id).where(
                ProjectMember.project_id == project_id,
                ProjectMember.user_id.in_(body.assignee_ids),
            )
        )
        valid_ids = {row[0] for row in result}
        invalid = set(body.assignee_ids) - valid_ids
        if invalid:
            raise ForbiddenError(
                "One or more assignees are not members of this project"
            )

    position = await get_next_position(project_id, status_id, db)

    task = Task(
        project_id=project_id,
        status_id=status_id,
        title=body.title,
        description=body.description,
        priority=body.priority,
        due_date=body.due_date,
        position=position,
    )
    db.add(task)
    await db.flush()

    for user_id in body.assignee_ids:
        db.add(
            TaskAssignee(
                task_id=task.id, user_id=user_id, assigned_by_id=current_user.id
            )
        )

    task_service.log_activity(
        db,
        project_id=project_id,
        task_id=task.id,
        user_id=current_user.id,
        action="task_created",
        new_value=body.title,
    )

    mentioned_ids = await resolve_mention_user_ids(
        parse_mentioned_usernames(body.description),
        project_id,
        current_user.id,
        db,
    )
    for uid in mentioned_ids:
        db.add(TaskMention(task_id=task.id, user_id=uid, actor_id=current_user.id))

    await db.commit()

    for uid in body.assignee_ids:
        if uid != current_user.id:
            await arq_pool.enqueue_job(
                "send_assignment_notification",
                user_id=uid,
                task_id=task.id,
                task_title=task.title,
                project_name=project.name,
            )
    for uid in mentioned_ids:
        await arq_pool.enqueue_job(
            "send_mention_notification",
            user_id=uid,
            actor_name=current_user.name,
            source_type="task",
            source_id=task.id,
            body_excerpt=(body.description or "")[:200],
        )

    return await get_task_or_404(task.id, db)


# --- List tasks ---


@project_tasks_router.get(
    "/{project_id}/tasks", response_model=CursorPage[TaskResponse]
)
async def list_tasks(
    project_id: int,
    status_id: int | None = Query(default=None),
    priority: TaskPriority | None = Query(default=None),
    assignee_id: int | None = Query(default=None),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CursorPage[TaskResponse]:
    await get_project_or_404(project_id, db)
    await get_member_or_403(project_id, current_user.id, db)

    cursor_data: dict | None = None
    if cursor is not None:
        cursor_data = decode_cursor(cursor)
        if cursor_data is None:
            raise ValidationError("Invalid cursor")

    query = (
        select(Task)
        .options(
            selectinload(Task.status),
            selectinload(Task.assignees),
            selectinload(Task.mentions),
        )
        .where(
            Task.project_id == project_id,
            Task.deleted_at.is_(None),
        )
    )

    if status_id is not None:
        query = query.where(Task.status_id == status_id)
    if priority is not None:
        query = query.where(Task.priority == priority)
    if assignee_id is not None:
        query = query.where(
            Task.task_assignees.any(TaskAssignee.user_id == assignee_id)
        )

    task_c = Task.__table__.c
    if cursor_data is not None:
        query = query.where(
            tuple_(task_c.status_id, task_c.position, task_c.id)
            > (
                int(cursor_data["status_id"]),
                int(cursor_data["position"]),
                int(cursor_data["id"]),
            )
        )

    query = query.order_by(asc(task_c.status_id), asc(task_c.position), asc(task_c.id))

    return await paginate_query(
        db,
        query,
        limit,
        lambda t: {"status_id": t.status_id, "position": t.position, "id": t.id},
    )


# --- Get task ---


@tasks_router.get("/{task_id}", response_model=TaskResponse)
async def get_task(
    task_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Task:
    task = await get_task_or_404(task_id, db)
    await get_member_or_403(task.project_id, current_user.id, db)
    return task


# --- Get task activity ---


@tasks_router.get("/{task_id}/activity", response_model=list[ActivityLogResponse])
async def get_task_activity(
    task_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[ActivityLog]:
    task = await get_task_or_404(task_id, db)
    await get_member_or_403(task.project_id, current_user.id, db)

    result = await db.execute(
        select(ActivityLog)
        .where(ActivityLog.task_id == task_id)
        .options(selectinload(ActivityLog.actor))
        .order_by(ActivityLog.created_at.asc())
    )
    return list(result.scalars().all())


async def _enqueue_update_notifications(
    arq_pool,
    *,
    task_id: int,
    task_title: str,
    project_name: str,
    newly_added_ids: set[int],
    status_notify_ids: set[int],
    new_status: TaskStatus | None,
    old_status_name: str,
) -> None:
    if status_notify_ids and new_status is not None:
        for uid in status_notify_ids:
            await arq_pool.enqueue_job(
                "send_status_change_notification",
                user_id=uid,
                task_id=task_id,
                task_title=task_title,
                project_name=project_name,
                old_status=old_status_name,
                new_status=new_status.name,
            )
    for uid in newly_added_ids:
        await arq_pool.enqueue_job(
            "send_assignment_notification",
            user_id=uid,
            task_id=task_id,
            task_title=task_title,
            project_name=project_name,
        )


# --- Update task helpers ---


async def _resolve_status_change(
    db: AsyncSession,
    task: Task,
    update_data: dict,
) -> TaskStatus | None:
    """Validate a status_id change and place the task at the tail of the new column.

    Returns the new TaskStatus if the status is actually changing, None otherwise.
    As a side effect, updates task.position when the column changes.
    """
    if "status_id" not in update_data:
        return None
    if update_data["status_id"] is None:
        raise ValidationError(
            "status_id cannot be cleared - a task must always have a status"
        )
    if update_data["status_id"] == task.status_id:
        return None
    result = await db.execute(
        select(TaskStatus).where(
            TaskStatus.id == update_data["status_id"],
            TaskStatus.project_id == task.project_id,
        )
    )
    new_status = result.scalar_one_or_none()
    if new_status is None:
        raise NotFoundError("Status not found in this project")
    task.position = await get_next_position(
        task.project_id, update_data["status_id"], db
    )
    return new_status


async def _resolve_assignee_change(
    db: AsyncSession,
    task: Task,
    update_data: dict,
    current_user_id: int,
) -> set[int]:
    """Validate that every proposed assignee is a project member.

    Returns the set of user IDs being newly added (the acting user excluded).
    """
    if not update_data.get("assignee_ids"):
        return set()
    result = await db.execute(
        select(ProjectMember.user_id).where(
            ProjectMember.project_id == task.project_id,
            ProjectMember.user_id.in_(update_data["assignee_ids"]),
        )
    )
    valid_ids = {row[0] for row in result}
    invalid = set(update_data["assignee_ids"]) - valid_ids
    if invalid:
        raise ForbiddenError("One or more assignees are not members of this project")
    old_ids = {ta.user_id for ta in task.task_assignees}
    return set(update_data["assignee_ids"]) - old_ids - {current_user_id}


async def _apply_mention_diff(
    db: AsyncSession,
    task_id: int,
    task: Task,
    update_data: dict,
    project_id: int,
    current_user_id: int,
) -> set[int]:
    """Reconcile TaskMention rows when the description changes.

    Deletes rows for users no longer mentioned, inserts rows for newly mentioned
    users. Returns the set of newly added mention user IDs so the caller can
    enqueue notification jobs after the commit.
    """
    if "description" not in update_data:
        return set()
    existing_ids = {m.user_id for m in task.mention_records}
    new_ids = await resolve_mention_user_ids(
        parse_mentioned_usernames(update_data["description"]),
        project_id,
        current_user_id,
        db,
    )
    removed = existing_ids - new_ids
    added = new_ids - existing_ids
    if removed:
        await db.execute(
            delete(TaskMention).where(
                TaskMention.task_id == task_id,
                TaskMention.user_id.in_(removed),
            )
        )
    for uid in added:
        db.add(TaskMention(task_id=task_id, user_id=uid, actor_id=current_user_id))
    return added


# --- Update task ---


@tasks_router.patch("/{task_id}", response_model=TaskResponse)
async def update_task(
    task_id: int,
    body: TaskUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    arq_pool=Depends(get_arq_pool),
) -> Task:
    task = await get_task_or_404(task_id, db)
    member = await get_member_or_403(task.project_id, current_user.id, db)

    update_data = body.model_dump(exclude_unset=True)

    if member.role in (ProjectRole.OWNER, ProjectRole.MANAGER):
        allowed_fields = {
            "title",
            "description",
            "status_id",
            "assignee_ids",
            "priority",
            "due_date",
        }
    else:
        current_assignee_ids = {ta.user_id for ta in task.task_assignees}
        if current_user.id not in current_assignee_ids:
            raise ForbiddenError("You can only update tasks assigned to you")
        allowed_fields = {"status_id", "description"}

    disallowed = set(update_data.keys()) - allowed_fields
    if disallowed:
        raise ForbiddenError("You do not have permission to update these fields")

    old_assignee_ids = {ta.user_id for ta in task.task_assignees}
    old_status_name = task.status.name

    new_status = await _resolve_status_change(db, task, update_data)
    newly_added_ids = await _resolve_assignee_change(
        db, task, update_data, current_user.id
    )
    mention_added_ids = await _apply_mention_diff(
        db, task_id, task, update_data, task.project_id, current_user.id
    )

    status_notify_ids = (
        old_assignee_ids - {current_user.id} if new_status is not None else set()
    )

    project_name: str | None = None
    if newly_added_ids or status_notify_ids:
        result = await db.execute(
            select(Project.name).where(Project.id == task.project_id)
        )
        project_name = result.scalar_one()

    await task_service.update_task(db, task, body, current_user, new_status)
    await db.commit()
    db.expunge(task)

    if project_name is not None:
        await _enqueue_update_notifications(
            arq_pool,
            task_id=task.id,
            task_title=task.title,
            project_name=project_name,
            newly_added_ids=newly_added_ids,
            status_notify_ids=status_notify_ids,
            new_status=new_status,
            old_status_name=old_status_name,
        )

    refreshed = await get_task_or_404(task.id, db)
    for uid in mention_added_ids:
        await arq_pool.enqueue_job(
            "send_mention_notification",
            user_id=uid,
            actor_name=current_user.name,
            source_type="task",
            source_id=task_id,
            body_excerpt=(update_data.get("description") or "")[:200],
        )

    return refreshed


# --- Reorder task ---


@tasks_router.patch("/{task_id}/position", response_model=TaskResponse)
async def reorder_task(
    task_id: int,
    body: TaskReorder,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Task:
    task = await get_task_or_404(task_id, db)
    member = await get_member_or_403(task.project_id, current_user.id, db)

    if member.role not in (ProjectRole.OWNER, ProjectRole.MANAGER):
        raise ForbiddenError("Only owners and managers can reorder tasks")

    # Validate target status belongs to this project
    result = await db.execute(
        select(TaskStatus).where(
            TaskStatus.id == body.status_id,
            TaskStatus.project_id == task.project_id,
        )
    )
    if result.scalar_one_or_none() is None:
        raise NotFoundError("Status not found in this project")

    old_position = task.position
    old_status_id = task.status_id
    new_status_id = body.status_id

    result = await db.execute(
        select(func.count()).where(
            Task.project_id == task.project_id,
            Task.status_id == new_status_id,
            Task.deleted_at.is_(None),
        )
    )
    column_count: int = result.scalar() or 0

    # Same column: task is already in it, so max stays at column_count
    # Different column: task will be added, so max is column_count + 1
    max_position = column_count if new_status_id == old_status_id else column_count + 1
    new_position = min(body.position, max_position)

    if new_status_id == old_status_id and new_position == old_position:
        return task

    if new_status_id == old_status_id:
        if new_position < old_position:
            await db.execute(
                update(Task)
                .where(
                    Task.project_id == task.project_id,
                    Task.status_id == old_status_id,
                    Task.position >= new_position,
                    Task.position < old_position,
                    Task.id != task.id,
                    Task.deleted_at.is_(None),
                )
                .values(position=Task.position + 1)
            )
        else:
            await db.execute(
                update(Task)
                .where(
                    Task.project_id == task.project_id,
                    Task.status_id == old_status_id,
                    Task.position > old_position,
                    Task.position <= new_position,
                    Task.id != task.id,
                    Task.deleted_at.is_(None),
                )
                .values(position=Task.position - 1)
            )
    else:
        await db.execute(
            update(Task)
            .where(
                Task.project_id == task.project_id,
                Task.status_id == old_status_id,
                Task.position > old_position,
                Task.deleted_at.is_(None),
            )
            .values(position=Task.position - 1)
        )
        await db.execute(
            update(Task)
            .where(
                Task.project_id == task.project_id,
                Task.status_id == new_status_id,
                Task.position >= new_position,
                Task.deleted_at.is_(None),
            )
            .values(position=Task.position + 1)
        )

    task.status_id = new_status_id
    task.position = new_position

    await db.commit()
    db.expunge(task)
    return await get_task_or_404(task.id, db)


# --- Delete task (soft) ---


@tasks_router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_task(
    task_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    task = await get_task_or_404(task_id, db)
    member = await get_member_or_403(task.project_id, current_user.id, db)

    if member.role not in (ProjectRole.OWNER, ProjectRole.MANAGER):
        raise ForbiddenError("Only owners and managers can delete tasks")

    task.deleted_at = datetime.now(UTC)
    await db.commit()
