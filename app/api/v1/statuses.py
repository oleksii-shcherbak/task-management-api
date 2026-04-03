from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status
from redis.asyncio import Redis
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    get_current_user,
    get_member_or_403_cached,
    get_project_or_404,
    invalidate_status_cache,
)
from app.core.cache import get_redis
from app.core.exceptions import (
    ConflictError,
    ForbiddenError,
    NotFoundError,
    ValidationError,
)
from app.database import get_db
from app.models.project_member import ProjectRole
from app.models.task import Task
from app.models.task_status import TaskStatus
from app.models.user import User
from app.schemas.status import StatusCreate, StatusUpdate
from app.schemas.task import TaskStatusResponse

router = APIRouter(prefix="/projects/{project_id}/statuses", tags=["statuses"])


async def _get_status_or_404(
    project_id: int, status_id: int, db: AsyncSession
) -> TaskStatus:
    result = await db.execute(
        select(TaskStatus).where(
            TaskStatus.id == status_id,
            TaskStatus.project_id == project_id,
        )
    )
    status_obj: TaskStatus | None = result.scalar_one_or_none()
    if status_obj is None:
        raise NotFoundError("Status not found")
    return status_obj


@router.post(
    "",
    response_model=TaskStatusResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create status",
    description="Add a new task status column to the project. Status names are unique per project (case-insensitive).",
    responses={
        401: {"description": "Not authenticated"},
        403: {"description": "Insufficient permissions"},
        404: {"description": "Project not found"},
        409: {"description": "Status name already exists"},
        422: {"description": "Validation error"},
    },
)
async def create_status(
    project_id: int,
    body: StatusCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> TaskStatus:
    await get_project_or_404(project_id, db)
    member = await get_member_or_403_cached(project_id, current_user.id, db, redis)

    if member.role not in (ProjectRole.OWNER, ProjectRole.MANAGER):
        raise ForbiddenError("Only owners and managers can manage statuses")

    existing = await db.execute(
        select(TaskStatus).where(
            TaskStatus.project_id == project_id,
            func.lower(TaskStatus.name) == body.name.lower(),
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise ConflictError(
            f"A status named '{body.name}' already exists in this project"
        )

    result = await db.execute(
        select(func.max(TaskStatus.position)).where(TaskStatus.project_id == project_id)
    )
    next_position = (result.scalar() or 0) + 1

    new_status = TaskStatus(
        project_id=project_id,
        name=body.name,
        color=body.color,
        type=body.type,
        position=next_position,
        is_default=False,
    )
    db.add(new_status)
    await db.commit()
    await db.refresh(new_status)

    await invalidate_status_cache(project_id, redis)

    return new_status


@router.patch(
    "/{status_id}",
    response_model=TaskStatusResponse,
    summary="Update status",
    description="Rename, recolor, reorder, or change the default status. Setting is_default=true promotes this status and demotes the current default.",
    responses={
        401: {"description": "Not authenticated"},
        403: {"description": "Insufficient permissions"},
        404: {"description": "Project or status not found"},
        409: {"description": "Status name already exists"},
        422: {"description": "Validation error"},
    },
)
async def update_status(
    project_id: int,
    status_id: int,
    body: StatusUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> TaskStatus:
    await get_project_or_404(project_id, db)
    member = await get_member_or_403_cached(project_id, current_user.id, db, redis)

    if member.role not in (ProjectRole.OWNER, ProjectRole.MANAGER):
        raise ForbiddenError("Only owners and managers can manage statuses")

    status_obj = await _get_status_or_404(project_id, status_id, db)

    if body.name is not None:
        duplicate = await db.execute(
            select(TaskStatus).where(
                TaskStatus.project_id == project_id,
                func.lower(TaskStatus.name) == body.name.lower(),
                TaskStatus.id != status_id,
            )
        )
        if duplicate.scalar_one_or_none() is not None:
            raise ConflictError(
                f"A status named '{body.name}' already exists in this project"
            )
        status_obj.name = body.name

    if body.color is not None:
        status_obj.color = body.color

    if body.is_default is not None:
        if not body.is_default:
            raise ValidationError(
                "Cannot unset the default directly. Set another status as default instead."
            )
        await db.execute(
            update(TaskStatus)
            .where(
                TaskStatus.project_id == project_id,
                TaskStatus.is_default.is_(True),
            )
            .values(is_default=False)
        )
        status_obj.is_default = True

    if body.position is not None:
        old_position = status_obj.position

        count_result = await db.execute(
            select(func.count()).where(TaskStatus.project_id == project_id)
        )
        total: int = count_result.scalar() or 0
        new_position = min(body.position, total)

        if new_position != old_position:
            if new_position < old_position:
                await db.execute(
                    update(TaskStatus)
                    .where(
                        TaskStatus.project_id == project_id,
                        TaskStatus.position >= new_position,
                        TaskStatus.position < old_position,
                    )
                    .values(position=TaskStatus.position + 1)
                )
            else:
                await db.execute(
                    update(TaskStatus)
                    .where(
                        TaskStatus.project_id == project_id,
                        TaskStatus.position > old_position,
                        TaskStatus.position <= new_position,
                    )
                    .values(position=TaskStatus.position - 1)
                )
            status_obj.position = new_position

    await db.commit()
    await db.refresh(status_obj)
    await invalidate_status_cache(project_id, redis)

    return status_obj


@router.delete(
    "/{status_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete status",
    description="Delete a status column. Supply move_tasks_to to migrate existing tasks first. The default status and the last remaining status cannot be deleted.",
    responses={
        401: {"description": "Not authenticated"},
        403: {"description": "Insufficient permissions"},
        404: {"description": "Project or status not found"},
        422: {
            "description": "Validation error (default or last status, tasks need migration)"
        },
    },
)
async def delete_status(
    project_id: int,
    status_id: int,
    move_tasks_to: int | None = Query(default=None, gt=0),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> None:
    await get_project_or_404(project_id, db)
    member = await get_member_or_403_cached(project_id, current_user.id, db, redis)

    if member.role not in (ProjectRole.OWNER, ProjectRole.MANAGER):
        raise ForbiddenError("Only owners and managers can manage statuses")

    status_obj = await _get_status_or_404(project_id, status_id, db)

    if status_obj.is_default:
        raise ValidationError(
            "Cannot delete the default status. Set another status as default first."
        )

    count_result = await db.execute(
        select(func.count()).where(TaskStatus.project_id == project_id)
    )
    if count_result.scalar() == 1:
        raise ValidationError("Cannot delete the only status in the project")

    if move_tasks_to is not None:
        if move_tasks_to == status_id:
            raise ValidationError(
                "move_tasks_to cannot refer to the status being deleted"
            )

        target_result = await db.execute(
            select(TaskStatus).where(
                TaskStatus.id == move_tasks_to,
                TaskStatus.project_id == project_id,
            )
        )
        if target_result.scalar_one_or_none() is None:
            raise NotFoundError("Target status not found in this project")

    task_count_result = await db.execute(
        select(func.count()).where(
            Task.status_id == status_id,
            Task.deleted_at.is_(None),
        )
    )
    if (task_count_result.scalar() or 0) > 0:
        if move_tasks_to is None:
            raise ValidationError(
                "This status has tasks. Provide move_tasks_to to migrate them before deleting."
            )
        await db.execute(
            update(Task)
            .where(Task.status_id == status_id, Task.deleted_at.is_(None))
            .values(status_id=move_tasks_to)
        )

    deleted_position = status_obj.position
    await db.delete(status_obj)

    # Close the position gap left by the deleted status.
    await db.execute(
        update(TaskStatus)
        .where(
            TaskStatus.project_id == project_id,
            TaskStatus.position > deleted_position,
        )
        .values(position=TaskStatus.position - 1)
    )

    await db.commit()
    await invalidate_status_cache(project_id, redis)
