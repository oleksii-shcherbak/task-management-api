from __future__ import annotations

from fastapi import APIRouter, Depends, status
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
from app.models.task_status import TaskStatus
from app.models.user import User
from app.schemas.status import StatusCreate, StatusUpdate
from app.schemas.task import TaskStatusResponse

router = APIRouter(prefix="/projects/{project_id}/statuses", tags=["statuses"])


@router.post("", response_model=TaskStatusResponse, status_code=status.HTTP_201_CREATED)
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


@router.patch("/{status_id}", response_model=TaskStatusResponse)
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

    result = await db.execute(
        select(TaskStatus).where(
            TaskStatus.id == status_id,
            TaskStatus.project_id == project_id,
        )
    )
    status_obj = result.scalar_one_or_none()
    if status_obj is None:
        raise NotFoundError("Status not found")

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
        if body.is_default is False:
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
        total = count_result.scalar()
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
