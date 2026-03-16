from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_current_user, get_member_or_403, get_project_or_404
from app.core.exceptions import ForbiddenError, NotFoundError, ValidationError
from app.database import get_db
from app.models.activity_log import ActivityLog
from app.models.project_member import ProjectMember, ProjectRole
from app.models.task import Task, TaskPriority
from app.models.task_assignee import TaskAssignee
from app.models.task_status import TaskStatus
from app.models.user import User
from app.schemas.activity_log import ActivityLogResponse
from app.schemas.task import TaskCreate, TaskReorder, TaskResponse, TaskUpdate
from app.services import task_service

project_tasks_router = APIRouter(prefix="/projects", tags=["tasks"])
tasks_router = APIRouter(prefix="/tasks", tags=["tasks"])


async def get_task_or_404(task_id: int, db: AsyncSession) -> Task:
    result = await db.execute(
        select(Task)
        .options(
            selectinload(Task.status),
            selectinload(Task.assignees),
            selectinload(Task.task_assignees).selectinload(TaskAssignee.user),
        )
        .where(
            Task.id == task_id,
            Task.deleted_at.is_(None),
        )
    )
    task = result.scalar_one_or_none()
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
) -> Task:
    await get_project_or_404(project_id, db)

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
        task_status = result.scalar_one_or_none()
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
        task_status = result.scalar_one_or_none()
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

    await db.commit()
    return await get_task_or_404(task.id, db)


# --- List tasks ---


@project_tasks_router.get("/{project_id}/tasks", response_model=list[TaskResponse])
async def list_tasks(
    project_id: int,
    status_id: int | None = Query(default=None),
    priority: TaskPriority | None = Query(default=None),
    assignee_id: int | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[Task]:
    await get_project_or_404(project_id, db)
    await get_member_or_403(project_id, current_user.id, db)

    query = (
        select(Task)
        .options(selectinload(Task.status), selectinload(Task.assignees))
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

    query = query.order_by(Task.status_id.asc(), Task.position.asc())

    result = await db.execute(query)
    return list(result.scalars().all())


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


# --- Update task ---


@tasks_router.patch("/{task_id}", response_model=TaskResponse)
async def update_task(
    task_id: int,
    body: TaskUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
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

    new_status: TaskStatus | None = None
    if "status_id" in update_data:
        if update_data["status_id"] is None:
            raise ValidationError(
                "status_id cannot be cleared — a task must always have a status"
            )
        if update_data["status_id"] != task.status_id:
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

    if update_data.get("assignee_ids"):
        result = await db.execute(
            select(ProjectMember.user_id).where(
                ProjectMember.project_id == task.project_id,
                ProjectMember.user_id.in_(update_data["assignee_ids"]),
            )
        )
        valid_ids = {row[0] for row in result}
        invalid = set(update_data["assignee_ids"]) - valid_ids
        if invalid:
            raise ForbiddenError(
                "One or more assignees are not members of this project"
            )

    await task_service.update_task(db, task, body, current_user, new_status)

    await db.commit()
    db.expunge(task)
    return await get_task_or_404(task.id, db)


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
    column_count = result.scalar()

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
