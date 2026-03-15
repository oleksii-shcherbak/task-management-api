from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import get_current_user, get_member_or_403, get_project_or_404
from app.core.exceptions import ForbiddenError, NotFoundError, ValidationError
from app.database import get_db
from app.models.project_member import ProjectMember, ProjectRole
from app.models.task import Task, TaskPriority
from app.models.task_status import TaskStatus
from app.models.user import User
from app.schemas.task import TaskCreate, TaskReorder, TaskResponse, TaskUpdate
from app.services import task_service

project_tasks_router = APIRouter(prefix="/projects", tags=["tasks"])
tasks_router = APIRouter(prefix="/tasks", tags=["tasks"])


async def get_task_or_404(task_id: int, db: AsyncSession) -> Task:
    result = await db.execute(
        select(Task)
        .options(selectinload(Task.status), selectinload(Task.assignee))
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

    # Validate assignee is a project member
    if body.assignee_id is not None:
        result = await db.execute(
            select(ProjectMember).where(
                ProjectMember.project_id == project_id,
                ProjectMember.user_id == body.assignee_id,
            )
        )
        if result.scalar_one_or_none() is None:
            raise ForbiddenError("Assignee is not a member of this project")

    position = await get_next_position(project_id, status_id, db)

    task = Task(
        project_id=project_id,
        status_id=status_id,
        assignee_id=body.assignee_id,
        title=body.title,
        description=body.description,
        priority=body.priority,
        due_date=body.due_date,
        position=position,
    )
    db.add(task)
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
        .options(selectinload(Task.status))
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
        query = query.where(Task.assignee_id == assignee_id)

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
            "assignee_id",
            "priority",
            "due_date",
        }
    else:
        # Regular members can only update their own assigned tasks, limited fields
        if task.assignee_id != current_user.id:
            raise ForbiddenError("You can only update tasks assigned to you")
        allowed_fields = {"status_id", "description"}

    disallowed = set(update_data.keys()) - allowed_fields
    if disallowed:
        raise ForbiddenError("You do not have permission to update these fields")

    # Validate new status and fetch the object for display name logging
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

    # Validate new assignee and fetch the User object for display name logging
    new_assignee: User | None = None
    if "assignee_id" in update_data and update_data["assignee_id"] is not None:
        result = await db.execute(
            select(ProjectMember).where(
                ProjectMember.project_id == task.project_id,
                ProjectMember.user_id == update_data["assignee_id"],
            )
        )
        if result.scalar_one_or_none() is None:
            raise ForbiddenError("Assignee is not a member of this project")
        result = await db.execute(
            select(User).where(User.id == update_data["assignee_id"])
        )
        new_assignee = result.scalar_one_or_none()

    # Delegate field updates and activity logging to the service
    await task_service.update_task(
        db, task, body, current_user, new_status, new_assignee
    )

    await db.commit()
    db.expunge(task)  # Detach from session to avoid stale data on return
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

    # Compute max valid position for the target column
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

    # Nothing to do
    if new_status_id == old_status_id and new_position == old_position:
        return task

    if new_status_id == old_status_id:
        # Same column — shift tasks between old and new position
        if new_position < old_position:
            # Moving up: tasks in [new, old) shift down
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
            # Moving down: tasks in (old, new] shift up
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
        # Different column — close gap in old column, make room in new column
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
    db.expunge(task)  # Detach from session to avoid stale data on return
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
