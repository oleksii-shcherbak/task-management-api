from datetime import UTC, datetime

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.exceptions import ForbiddenError, NotFoundError
from app.database import get_db
from app.models.project import Project
from app.models.project_member import ProjectMember, ProjectRole
from app.models.user import User
from app.schemas.project import ProjectCreate, ProjectResponse, ProjectUpdate

router = APIRouter(prefix="/projects", tags=["projects"])


async def get_project_or_404(project_id: int, db: AsyncSession) -> Project:
    """Fetch a project by ID, raise 404 if not found or soft-deleted."""
    result = await db.execute(
        select(Project).where(
            Project.id == project_id,
            Project.deleted_at.is_(None),
        )
    )
    project = result.scalar_one_or_none()
    if project is None:
        raise NotFoundError("Project not found")
    return project


async def get_member_or_403(
    project_id: int, user_id: int, db: AsyncSession
) -> ProjectMember:
    """Fetch membership record, raise 403 if user is not a member."""
    result = await db.execute(
        select(ProjectMember).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == user_id,
        )
    )
    member = result.scalar_one_or_none()
    if member is None:
        raise ForbiddenError("You are not a member of this project")
    return member


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
    await db.commit()
    await db.refresh(project)
    return project


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
