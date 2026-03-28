import uuid
from datetime import UTC, datetime
from pathlib import Path

import filetype
from fastapi import APIRouter, Depends, UploadFile
from sqlalchemy import select, update
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
from app.models.refresh_token import RefreshToken
from app.models.user import User
from app.schemas.user import (
    PasswordChange,
    PublicUserResponse,
    UserResponse,
    UserUpdate,
)

MAX_AVATAR_BYTES = 2 * 1024 * 1024  # 2 MB
ALLOWED_AVATAR_MIME_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}

router = APIRouter(prefix="/users", tags=["Users"])


@router.get("/me", response_model=UserResponse)
async def get_me(current_user: User = Depends(get_current_user)):
    return current_user


@router.patch("/me", response_model=UserResponse)
async def update_me(
    data: UserUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    updates = data.model_dump(exclude_unset=True)

    if "email" in updates and updates["email"] != current_user.email:
        result = await db.execute(select(User).where(User.email == updates["email"]))
        if result.scalar_one_or_none():
            raise ConflictError("Email already taken")
        current_user.is_verified = False

    if "username" in updates and updates["username"] != current_user.username:
        result = await db.execute(
            select(User.id).where(
                User.username == updates["username"], User.deleted_at.is_(None)
            )
        )
        if result.scalar_one_or_none():
            raise ConflictError("Username already taken")

    for key, value in updates.items():
        setattr(current_user, key, value)

    await db.commit()
    await db.refresh(current_user)
    return current_user


@router.patch("/me/password", status_code=204)
async def change_password(
    data: PasswordChange,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
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


@router.delete("/me", status_code=204)
async def delete_me(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    current_user.deleted_at = datetime.now(UTC)
    await db.commit()


@router.post("/me/avatar", response_model=UserResponse)
async def upload_avatar(
    file: UploadFile,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    storage: StorageService = Depends(get_storage_service),
):
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


@router.delete("/me/avatar", status_code=204)
async def delete_avatar(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    storage: StorageService = Depends(get_storage_service),
):
    if current_user.avatar_path:
        await storage.delete_file(current_user.avatar_path)

    current_user.avatar_path = None
    current_user.avatar_url = None
    await db.commit()


@router.get("/{user_id}", response_model=PublicUserResponse)
async def get_user(user_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(User).where(User.id == user_id, User.deleted_at.is_(None))
    )
    user = result.scalar_one_or_none()
    if user is None:
        raise NotFoundError("User not found")
    return user
