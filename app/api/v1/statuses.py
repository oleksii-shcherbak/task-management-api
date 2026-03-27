from __future__ import annotations

from fastapi import APIRouter, Depends, status
from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    get_current_user,
    get_member_or_403_cached,
    get_project_or_404,
    invalidate_status_cache,
)
from app.core.cache import get_redis
from app.core.exceptions import ConflictError, ForbiddenError
from app.database import get_db
from app.models.project_member import ProjectRole
from app.models.task_status import TaskStatus
from app.models.user import User
from app.schemas.status import StatusCreate
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
