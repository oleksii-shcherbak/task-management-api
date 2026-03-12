from datetime import UTC, datetime

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_member_or_403, get_project_or_404
from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError
from app.database import get_db
from app.models.project import Project
from app.models.project_member import ProjectMember, ProjectRole
from app.models.task_status import StatusType, TaskStatus
from app.models.user import User
from app.schemas.project import (
    MemberAddRequest,
    MemberResponse,
    MemberRoleUpdate,
    ProjectCreate,
    ProjectResponse,
    ProjectUpdate,
)
from app.schemas.task import TaskStatusResponse

router = APIRouter(prefix="/projects", tags=["projects"])


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
) -> list[TaskStatus]:
    await get_project_or_404(project_id, db)
    await get_member_or_403(project_id, current_user.id, db)

    result = await db.execute(
        select(TaskStatus)
        .where(TaskStatus.project_id == project_id)
        .order_by(TaskStatus.position.asc())
    )
    return list(result.scalars().all())


@router.get("", response_model=list[ProjectResponse])
async def list_projects(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[Project]:
    result = await db.execute(
        select(Project)
        .join(ProjectMember, ProjectMember.project_id == Project.id)
        .where(
            ProjectMember.user_id == current_user.id,
            Project.deleted_at.is_(None),
        )
    )
    return list(result.scalars().all())


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Project:
    project = await get_project_or_404(project_id, db)
    await get_member_or_403(project_id, current_user.id, db)
    return project


@router.patch("/{project_id}", response_model=ProjectResponse)
async def update_project(
    project_id: int,
    body: ProjectUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Project:
    project = await get_project_or_404(project_id, db)

    member = await get_member_or_403(project_id, current_user.id, db)
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
) -> None:
    project = await get_project_or_404(project_id, db)

    member = await get_member_or_403(project_id, current_user.id, db)
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
) -> ProjectMember:
    await get_project_or_404(project_id, db)

    member = await get_member_or_403(project_id, current_user.id, db)
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
    return new_member


@router.get("/{project_id}/members", response_model=list[MemberResponse])
async def list_members(
    project_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[ProjectMember]:
    await get_project_or_404(project_id, db)
    await get_member_or_403(project_id, current_user.id, db)

    result = await db.execute(
        select(ProjectMember).where(ProjectMember.project_id == project_id)
    )
    return list(result.scalars().all())


@router.delete(
    "/{project_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def remove_member(
    project_id: int,
    user_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    await get_project_or_404(project_id, db)

    member = await get_member_or_403(project_id, current_user.id, db)
    if member.role not in (ProjectRole.OWNER, ProjectRole.MANAGER):
        raise ForbiddenError("Only owners and managers can remove members")

    result = await db.execute(
        select(ProjectMember).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == user_id,
        )
    )
    target = result.scalar_one_or_none()
    if target is None:
        raise NotFoundError("Member not found")
    if target.role == ProjectRole.OWNER:
        raise ForbiddenError("Cannot remove the project owner")

    await db.delete(target)
    await db.commit()


@router.patch("/{project_id}/members/{user_id}/role", response_model=MemberResponse)
async def update_member_role(
    project_id: int,
    user_id: int,
    body: MemberRoleUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ProjectMember:
    await get_project_or_404(project_id, db)

    member = await get_member_or_403(project_id, current_user.id, db)
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
    target = result.scalar_one_or_none()
    if target is None:
        raise NotFoundError("Member not found")
    if target.role == ProjectRole.OWNER:
        raise ForbiddenError("Cannot change the owner's role")

    target.role = body.role
    await db.commit()
    await db.refresh(target)
    return target
