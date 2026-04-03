import json
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query, status
from redis.asyncio import Redis
from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    get_current_user,
    get_member_or_403_cached,
    get_project_or_404,
    invalidate_membership_cache,
)
from app.core.arq_pool import get_arq_pool
from app.core.cache import get_redis
from app.core.exceptions import (
    ConflictError,
    ForbiddenError,
    NotFoundError,
    ValidationError,
)
from app.database import get_db
from app.models.project import Project
from app.models.project_member import ProjectMember, ProjectRole
from app.models.task_status import StatusType, TaskStatus
from app.models.user import User
from app.schemas.project import (
    MemberAddRequest,
    MemberResponse,
    MemberRoleUpdate,
    MemberSearchResult,
    ProjectCreate,
    ProjectResponse,
    ProjectUpdate,
)
from app.schemas.task import TaskStatusResponse
from app.utils.pagination import CursorPage, decode_cursor, encode_cursor

router = APIRouter(prefix="/projects", tags=["Projects"])


@router.post("", status_code=status.HTTP_201_CREATED, response_model=ProjectResponse)
async def create_project(
    body: ProjectCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Project:
    project = Project(
        **body.model_dump(),
        owner_id=current_user.id,
    )
    db.add(project)
    await db.flush()

    membership = ProjectMember(
        project_id=project.id,
        user_id=current_user.id,
        role=ProjectRole.OWNER,
    )
    db.add(membership)

    default_statuses = [
        TaskStatus(
            project_id=project.id,
            name="Backlog",
            color="#94a3b8",
            position=1,
            type=StatusType.UNSTARTED,
            is_default=True,
        ),
        TaskStatus(
            project_id=project.id,
            name="In Progress",
            color="#3b82f6",
            position=2,
            type=StatusType.STARTED,
            is_default=False,
        ),
        TaskStatus(
            project_id=project.id,
            name="Done",
            color="#22c55e",
            position=3,
            type=StatusType.COMPLETED,
            is_default=False,
        ),
    ]
    db.add_all(default_statuses)

    await db.commit()
    await db.refresh(project)
    return project


@router.get("/{project_id}/statuses", response_model=list[TaskStatusResponse])
async def list_project_statuses(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> list[TaskStatus]:
    await get_project_or_404(project_id, db)
    await get_member_or_403_cached(project_id, current_user.id, db, redis)

    key = f"statuses:{project_id}"
    cached = await redis.get(key)
    if cached is not None:
        statuses = []
        for s in json.loads(cached):
            status_obj = TaskStatus()
            status_obj.id = s["id"]
            status_obj.project_id = s["project_id"]
            status_obj.name = s["name"]
            status_obj.color = s["color"]
            status_obj.position = s["position"]
            status_obj.type = StatusType(s["type"])
            status_obj.is_default = s["is_default"]
            statuses.append(status_obj)
        return statuses

    result = await db.execute(
        select(TaskStatus)
        .where(TaskStatus.project_id == project_id)
        .order_by(TaskStatus.position.asc())
    )
    statuses = list(result.scalars().all())
    await redis.set(
        key,
        json.dumps(
            [
                {
                    "id": s.id,
                    "project_id": s.project_id,
                    "name": s.name,
                    "color": s.color,
                    "position": s.position,
                    "type": s.type.value,
                    "is_default": s.is_default,
                }
                for s in statuses
            ]
        ),
        ex=600,  # cache for 10 minutes
    )
    return statuses


@router.get("", response_model=CursorPage[ProjectResponse])
async def list_projects(
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> CursorPage[ProjectResponse]:
    cursor_data: dict | None = None
    if cursor is not None:
        cursor_data = decode_cursor(cursor)
        if cursor_data is None:
            raise ValidationError("Invalid cursor")

    query = (
        select(Project)
        .join(ProjectMember, ProjectMember.project_id == Project.id)
        .where(
            ProjectMember.user_id == current_user.id,
            Project.deleted_at.is_(None),
        )
    )

    if cursor_data is not None:
        cursor_created_at = datetime.fromisoformat(cursor_data["created_at"])
        query = query.where(
            tuple_(Project.created_at, Project.id)
            > (cursor_created_at, cursor_data["id"])
        )

    query = query.order_by(Project.created_at.asc(), Project.id.asc()).limit(limit + 1)

    result = await db.execute(query)
    projects = list(result.scalars().all())

    has_more = len(projects) > limit
    if has_more:
        projects = projects[:limit]

    next_cursor: str | None = None
    if has_more:
        last = projects[-1]
        next_cursor = encode_cursor(
            {"created_at": last.created_at.isoformat(), "id": last.id}
        )

    return CursorPage(items=projects, next_cursor=next_cursor, has_more=has_more)


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> Project:
    project = await get_project_or_404(project_id, db)
    await get_member_or_403_cached(project_id, current_user.id, db, redis)
    return project


@router.patch("/{project_id}", response_model=ProjectResponse)
async def update_project(
    project_id: int,
    body: ProjectUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> Project:
    project = await get_project_or_404(project_id, db)

    member = await get_member_or_403_cached(project_id, current_user.id, db, redis)
    if member.role not in (ProjectRole.OWNER, ProjectRole.MANAGER):
        raise ForbiddenError("Only project owners and managers can update the project")

    update_data = body.model_dump(
        exclude_unset=True
    )  # only update fields the client actually sent
    for field, value in update_data.items():
        setattr(project, field, value)

    await db.commit()
    await db.refresh(project)
    return project


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> None:
    project = await get_project_or_404(project_id, db)

    member = await get_member_or_403_cached(project_id, current_user.id, db, redis)
    if member.role != ProjectRole.OWNER:
        raise ForbiddenError("Only the project owner can delete the project")

    project.deleted_at = datetime.now(UTC)
    await db.commit()


@router.post(
    "/{project_id}/members",
    status_code=status.HTTP_201_CREATED,
    response_model=MemberResponse,
)
async def add_member(
    project_id: int,
    body: MemberAddRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
    arq_pool=Depends(get_arq_pool),
) -> ProjectMember:
    project = await get_project_or_404(project_id, db)

    member = await get_member_or_403_cached(project_id, current_user.id, db, redis)
    if member.role not in (ProjectRole.OWNER, ProjectRole.MANAGER):
        raise ForbiddenError("Only owners and managers can add members")

    if body.role == ProjectRole.OWNER:
        raise ForbiddenError("Cannot assign owner role via this endpoint")

    result = await db.execute(select(User).where(User.id == body.user_id))
    target_user = result.scalar_one_or_none()
    if target_user is None:
        raise NotFoundError("User not found")

    result = await db.execute(
        select(ProjectMember).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == body.user_id,
        )
    )
    if result.scalar_one_or_none() is not None:
        raise ConflictError("User is already a member of this project")

    new_member = ProjectMember(
        project_id=project_id,
        user_id=body.user_id,
        role=body.role,
    )
    db.add(new_member)
    await db.commit()
    await db.refresh(new_member)

    await arq_pool.enqueue_job(
        "send_project_invitation",
        user_id=body.user_id,
        project_name=project.name,
        role=body.role.value,
    )
    return new_member


@router.get("/{project_id}/members", response_model=list[MemberResponse])
async def list_members(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> list[ProjectMember]:
    await get_project_or_404(project_id, db)
    await get_member_or_403_cached(project_id, current_user.id, db, redis)

    result = await db.execute(
        select(ProjectMember).where(ProjectMember.project_id == project_id)
    )
    return list(result.scalars().all())


@router.get("/{project_id}/members/search", response_model=list[MemberSearchResult])
async def search_members(
    project_id: int,
    q: str = Query(min_length=1),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> list[MemberSearchResult]:
    await get_project_or_404(project_id, db)
    await get_member_or_403_cached(project_id, current_user.id, db, redis)

    result = await db.execute(
        select(User)
        .join(ProjectMember, ProjectMember.user_id == User.id)
        .where(
            ProjectMember.project_id == project_id,
            User.deleted_at.is_(None),
            User.username.ilike(f"{q}%"),
        )
        .order_by(User.username)
        .limit(10)
    )
    users = result.scalars().all()
    return [
        MemberSearchResult(
            user_id=u.id,
            username=u.username,
            full_name=u.name,
            avatar_url=u.avatar_url,
        )
        for u in users
    ]


@router.delete(
    "/{project_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def remove_member(
    project_id: int,
    user_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> None:
    await get_project_or_404(project_id, db)

    member = await get_member_or_403_cached(project_id, current_user.id, db, redis)
    if member.role not in (ProjectRole.OWNER, ProjectRole.MANAGER):
        raise ForbiddenError("Only owners and managers can remove members")

    result = await db.execute(
        select(ProjectMember).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == user_id,
        )
    )
    target: ProjectMember | None = result.scalar_one_or_none()
    if target is None:
        raise NotFoundError("Member not found")
    if target.role == ProjectRole.OWNER:
        raise ForbiddenError("Cannot remove the project owner")

    await db.delete(target)
    await db.commit()
    await invalidate_membership_cache(project_id, user_id, redis)


@router.patch("/{project_id}/members/{user_id}/role", response_model=MemberResponse)
async def update_member_role(
    project_id: int,
    user_id: int,
    body: MemberRoleUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> ProjectMember:
    await get_project_or_404(project_id, db)

    member = await get_member_or_403_cached(project_id, current_user.id, db, redis)
    if member.role != ProjectRole.OWNER:
        raise ForbiddenError("Only the project owner can change member roles")

    if body.role == ProjectRole.OWNER:
        raise ForbiddenError("Cannot assign owner role via this endpoint")

    result = await db.execute(
        select(ProjectMember).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == user_id,
        )
    )
    target: ProjectMember | None = result.scalar_one_or_none()
    if target is None:
        raise NotFoundError("Member not found")
    if target.role == ProjectRole.OWNER:
        raise ForbiddenError("Cannot change the owner's role")

    target.role = body.role
    await db.commit()
    await db.refresh(target)
    await invalidate_membership_cache(project_id, user_id, redis)
    return target
