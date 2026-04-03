import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import filetype
from fastapi import APIRouter, Depends, Query, UploadFile
from sqlalchemy import and_, func, literal, or_, select, union_all, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.exceptions import (
    ConflictError,
    NotFoundError,
    UnauthorizedError,
    ValidationError,
)
from app.core.security import hash_password, verify_password
from app.core.storage import StorageService, get_storage_service
from app.database import get_db
from app.models.comment import Comment
from app.models.comment_mention import CommentMention
from app.models.project import Project
from app.models.refresh_token import RefreshToken
from app.models.task import Task
from app.models.task_mention import TaskMention
from app.models.user import User
from app.models.username_history import UsernameHistory
from app.schemas.user import (
    MentionInboxItem,
    PasswordChange,
    PublicUserResponse,
    UserResponse,
    UserUpdate,
)
from app.utils.pagination import CursorPage, decode_cursor, encode_cursor

USERNAME_COOLDOWN_DAYS = 30

MAX_AVATAR_BYTES = 2 * 1024 * 1024  # 2 MB
ALLOWED_AVATAR_MIME_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}

router = APIRouter(prefix="/users", tags=["Users"])


@router.get(
    "/me",
    response_model=UserResponse,
    summary="Get current user",
    responses={401: {"description": "Not authenticated"}},
)
async def get_me(current_user: User = Depends(get_current_user)) -> User:
    return current_user


@router.patch(
    "/me",
    response_model=UserResponse,
    summary="Update profile",
    description="Partial update of profile fields. Changing email resets verification status; username changes are limited to once per 30 days.",
    responses={
        401: {"description": "Not authenticated"},
        409: {"description": "Email or username already taken"},
        422: {"description": "Validation error"},
    },
)
async def update_me(
    data: UserUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> User:
    updates = data.model_dump(exclude_unset=True)

    if "email" in updates and updates["email"] != current_user.email:
        result = await db.execute(select(User).where(User.email == updates["email"]))
        if result.scalar_one_or_none():
            raise ConflictError("Email already taken")
        current_user.is_verified = False

    if "username" in updates and updates["username"] != current_user.username:
        new_username = updates["username"]

        # Enforce one change per 30 days
        recent = await db.execute(
            select(UsernameHistory)
            .where(UsernameHistory.user_id == current_user.id)
            .order_by(UsernameHistory.changed_at.desc())
            .limit(1)
        )
        last_change: UsernameHistory | None = recent.scalar_one_or_none()
        if last_change is not None:
            next_allowed = last_change.changed_at + timedelta(
                days=USERNAME_COOLDOWN_DAYS
            )
            if datetime.now(UTC) < next_allowed:
                raise ValidationError(
                    f"Username can only be changed once every {USERNAME_COOLDOWN_DAYS} days. "
                    f"Next change allowed after {next_allowed.date().isoformat()}"
                )

        # Check active users
        taken_by_active = await db.execute(
            select(User.id).where(
                User.username == new_username, User.deleted_at.is_(None)
            )
        )
        if taken_by_active.scalar_one_or_none():
            raise ConflictError("Username already taken")

        # Check username_history grace period (released_at > now means still reserved)
        reserved = await db.execute(
            select(UsernameHistory.id).where(
                UsernameHistory.old_username == new_username,
                UsernameHistory.released_at > datetime.now(UTC),
            )
        )
        if reserved.scalar_one_or_none():
            raise ConflictError("Username is temporarily reserved")

        db.add(
            UsernameHistory(
                user_id=current_user.id,
                old_username=current_user.username,
                changed_at=datetime.now(UTC),
                released_at=datetime.now(UTC) + timedelta(days=USERNAME_COOLDOWN_DAYS),
            )
        )

    for key, value in updates.items():
        setattr(current_user, key, value)

    await db.commit()
    await db.refresh(current_user)
    return current_user


@router.patch(
    "/me/password",
    status_code=204,
    summary="Change password",
    description="Verify the current password, set a new one, and revoke all active refresh tokens.",
    responses={
        401: {"description": "Not authenticated or current password incorrect"},
        422: {"description": "Validation error"},
    },
)
async def change_password(
    data: PasswordChange,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    if not verify_password(data.current_password, current_user.password_hash):
        raise UnauthorizedError("Current password is incorrect")

    current_user.password_hash = hash_password(data.new_password)
    current_user.password_changed_at = datetime.now(UTC)

    await db.execute(
        update(RefreshToken)
        .where(
            RefreshToken.user_id == current_user.id, RefreshToken.is_revoked.is_(False)
        )
        .values(is_revoked=True)
    )
    await db.commit()


@router.delete(
    "/me",
    status_code=204,
    summary="Delete account",
    description="Soft-delete the current user. The account is flagged with a timestamp and excluded from all lookups.",
    responses={401: {"description": "Not authenticated"}},
)
async def delete_me(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    current_user.deleted_at = datetime.now(UTC)
    await db.commit()


@router.post(
    "/me/avatar",
    response_model=UserResponse,
    summary="Upload avatar",
    description="Replace the current avatar. Accepts JPEG, PNG, GIF, or WebP up to 2 MB. The previous file is deleted from storage.",
    responses={
        401: {"description": "Not authenticated"},
        422: {"description": "File too large or unsupported format"},
    },
)
async def upload_avatar(
    file: UploadFile,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    storage: StorageService = Depends(get_storage_service),
) -> User:
    data = await file.read()
    if len(data) > MAX_AVATAR_BYTES:
        raise ValidationError("Avatar exceeds the 2 MB limit")

    kind = filetype.guess(data)
    if kind is None or kind.mime not in ALLOWED_AVATAR_MIME_TYPES:
        raise ValidationError("Avatar must be a JPEG, PNG, GIF, or WebP image")

    if current_user.avatar_path:
        await storage.delete_file(current_user.avatar_path)

    ext = Path(file.filename or "").suffix.lower() or f".{kind.extension}"
    storage_path = f"avatars/{uuid.uuid4()}{ext}"
    await storage.upload_file(data, storage_path)

    current_user.avatar_path = storage_path
    current_user.avatar_url = storage.get_url(storage_path)
    await db.commit()
    await db.refresh(current_user)
    return current_user


@router.delete(
    "/me/avatar",
    status_code=204,
    summary="Delete avatar",
    responses={401: {"description": "Not authenticated"}},
)
async def delete_avatar(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    storage: StorageService = Depends(get_storage_service),
) -> None:
    if current_user.avatar_path:
        await storage.delete_file(current_user.avatar_path)

    current_user.avatar_path = None
    current_user.avatar_url = None
    await db.commit()


@router.get(
    "/me/mentions",
    response_model=CursorPage[MentionInboxItem],
    summary="Get mention inbox",
    description="Cursor-paginated list of task descriptions and comments that @mention the current user, sorted newest first.",
    responses={
        401: {"description": "Not authenticated"},
        422: {"description": "Invalid cursor"},
    },
)
async def get_my_mentions(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    cursor: str | None = Query(None),
    limit: int = Query(20, ge=1, le=100),
):
    cursor_data = decode_cursor(cursor) if cursor else None
    if cursor and cursor_data is None:
        raise ValidationError("Invalid cursor")

    comment_q = (
        select(
            literal("comment").label("source_type"),
            Comment.id.label("source_id"),
            Task.id.label("task_id"),
            Project.id.label("project_id"),
            Project.name.label("project_name"),
            User.name.label("actor_name"),
            User.username.label("actor_username"),
            func.left(Comment.content, 200).label("body_excerpt"),
            Comment.created_at.label("created_at"),
        )
        .join(Task, Task.id == Comment.task_id)
        .join(Project, Project.id == Task.project_id)
        .join(CommentMention, CommentMention.comment_id == Comment.id)
        .join(User, User.id == CommentMention.actor_id)
        .where(
            CommentMention.user_id == current_user.id,
            CommentMention.actor_id.is_not(None),
            Task.deleted_at.is_(None),
        )
    )

    task_q = (
        select(
            literal("task").label("source_type"),
            Task.id.label("source_id"),
            Task.id.label("task_id"),
            Project.id.label("project_id"),
            Project.name.label("project_name"),
            User.name.label("actor_name"),
            User.username.label("actor_username"),
            func.left(func.coalesce(Task.description, ""), 200).label("body_excerpt"),
            Task.created_at.label("created_at"),
        )
        .join(Project, Project.id == Task.project_id)
        .join(TaskMention, TaskMention.task_id == Task.id)
        .join(User, User.id == TaskMention.actor_id)
        .where(
            TaskMention.user_id == current_user.id,
            TaskMention.actor_id.is_not(None),
            Task.deleted_at.is_(None),
        )
    )

    combined = union_all(comment_q, task_q).subquery()
    q = select(combined)

    if cursor_data is not None:
        cursor_at = datetime.fromisoformat(cursor_data["created_at"])
        cs = cursor_data["source_type"]
        ci = cursor_data["source_id"]
        q = q.where(
            or_(
                combined.c.created_at < cursor_at,
                and_(
                    combined.c.created_at == cursor_at,
                    or_(
                        combined.c.source_type > cs,
                        and_(combined.c.source_type == cs, combined.c.source_id > ci),
                    ),
                ),
            )
        )

    q = q.order_by(
        combined.c.created_at.desc(),
        combined.c.source_type.asc(),
        combined.c.source_id.asc(),
    ).limit(limit + 1)

    rows = list((await db.execute(q)).all())

    has_more = len(rows) > limit
    if has_more:
        rows = rows[:limit]

    items = [
        MentionInboxItem(
            source_type=row.source_type,
            task_id=row.task_id,
            project_id=row.project_id,
            project_name=row.project_name,
            actor_name=row.actor_name,
            actor_username=row.actor_username,
            body_excerpt=row.body_excerpt,
            created_at=row.created_at,
        )
        for row in rows
    ]

    next_cursor: str | None = None
    if has_more:
        last = rows[-1]
        next_cursor = encode_cursor(
            {
                "created_at": last.created_at.isoformat(),
                "source_type": last.source_type,
                "source_id": last.source_id,
            }
        )

    return CursorPage(items=items, next_cursor=next_cursor, has_more=has_more)


@router.get(
    "/{user_id}",
    response_model=PublicUserResponse,
    summary="Get user profile",
    description="Public profile for any non-deleted user. Returns id, name, username, and avatar only.",
    responses={404: {"description": "User not found"}},
)
async def get_user(user_id: int, db: AsyncSession = Depends(get_db)) -> User:
    result = await db.execute(
        select(User).where(User.id == user_id, User.deleted_at.is_(None))
    )
    user: User | None = result.scalar_one_or_none()
    if user is None:
        raise NotFoundError("User not found")
    return user
