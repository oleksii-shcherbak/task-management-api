import json
from datetime import UTC, datetime

import jwt
import structlog
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ForbiddenError, NotFoundError
from app.core.security import decode_access_token
from app.database import get_db
from app.models.project import Project
from app.models.project_member import ProjectMember, ProjectRole
from app.models.user import User

logger = structlog.get_logger()

_MEMBERSHIP_CACHE_TTL = 300  # 5 minutes

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    try:
        payload = decode_access_token(token)
        user_id: str | None = payload.get("sub")
        if user_id is None:
            logger.warning("invalid_token", reason="missing_sub")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid authentication credentials",
                headers={"WWW-Authenticate": "Bearer"},
            )
    except jwt.ExpiredSignatureError:
        logger.warning("token_expired")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        ) from None
    except jwt.InvalidTokenError:
        logger.warning("invalid_token", reason="malformed")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        ) from None

    result = await db.execute(select(User).where(User.id == int(user_id)))
    user: User | None = result.scalar_one_or_none()
    if user is None or user.deleted_at is not None:
        logger.warning("auth_failure", reason="user_not_found", user_id=user_id)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token_iat: float | None = payload.get("iat")
    if token_iat and datetime.fromtimestamp(
        token_iat, UTC
    ) < user.password_changed_at.replace(microsecond=0):
        logger.warning("token_invalidated", user_id=user.id)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token invalidated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    structlog.contextvars.bind_contextvars(user_id=user.id)
    return user


async def get_project_or_404(project_id: int, db: AsyncSession) -> Project:
    result = await db.execute(
        select(Project).where(
            Project.id == project_id,
            Project.deleted_at.is_(None),
        )
    )
    project: Project | None = result.scalar_one_or_none()
    if project is None:
        raise NotFoundError("Project not found")
    return project


async def get_member_or_403(
    project_id: int, user_id: int, db: AsyncSession
) -> ProjectMember:
    result = await db.execute(
        select(ProjectMember).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == user_id,
        )
    )
    member: ProjectMember | None = result.scalar_one_or_none()
    if member is None:
        raise ForbiddenError("You are not a member of this project")
    return member


async def get_member_or_403_cached(
    project_id: int, user_id: int, db: AsyncSession, redis: Redis
) -> ProjectMember:
    """Check project membership, using Redis as a read-through cache.

    Only the role is stored - callers never need joined_at or other fields.
    On cache miss the result is written back so subsequent calls skip the DB.
    """
    key = f"membership:{project_id}:{user_id}"
    cached = await redis.get(key)
    if cached is not None:
        member = ProjectMember()
        member.project_id = project_id
        member.user_id = user_id
        member.role = ProjectRole(json.loads(cached)["role"])
        return member

    member = await get_member_or_403(project_id, user_id, db)
    await redis.set(
        key, json.dumps({"role": member.role.value}), ex=_MEMBERSHIP_CACHE_TTL
    )
    return member


async def invalidate_membership_cache(
    project_id: int, user_id: int, redis: Redis
) -> None:
    await redis.delete(f"membership:{project_id}:{user_id}")


async def invalidate_status_cache(project_id: int, redis: Redis) -> None:
    await redis.delete(f"statuses:{project_id}")
