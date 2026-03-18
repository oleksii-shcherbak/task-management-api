from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.exceptions import ConflictError, UnauthorizedError
from app.core.security import (
    create_access_token,
    generate_refresh_token,
    hash_password,
    hash_token,
    verify_password,
)
from app.database import get_db
from app.models.refresh_token import RefreshToken
from app.models.user import User
from app.schemas.auth import (
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    TokenResponse,
)

router = APIRouter(prefix="/auth", tags=["Authentication"])


def _prepare_token_response(user_id: int, db: AsyncSession) -> TokenResponse:
    """Stage a fresh access + refresh token pair on the session (caller must commit)."""
    access_token = create_access_token({"sub": str(user_id)})
    plain_refresh_token = generate_refresh_token()
    db.add(
        RefreshToken(
            token_hash=hash_token(plain_refresh_token),
            user_id=user_id,
            expires_at=datetime.now(UTC)
            + timedelta(days=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS),
        )
    )
    return TokenResponse(access_token=access_token, refresh_token=plain_refresh_token)


@router.post("/register", response_model=TokenResponse, status_code=201)
async def register(data: RegisterRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(User).where(User.email == data.email, User.deleted_at.is_(None))
    )
    if result.scalar_one_or_none():
        raise ConflictError("Email already registered")

    user = User(
        email=data.email,
        password_hash=hash_password(data.password),
        name=data.name,
        password_changed_at=datetime.now(UTC),
    )
    db.add(user)
    await db.flush()  # populate user.id before staging the refresh token

    response = _prepare_token_response(user.id, db)
    await db.commit()
    return response


@router.post("/login", response_model=TokenResponse)
async def login(data: LoginRequest, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(User).where(User.email == data.email, User.deleted_at.is_(None))
    )
    user = result.scalar_one_or_none()

    if not user or not verify_password(data.password, user.password_hash):
        raise UnauthorizedError("Invalid email or password")

    response = _prepare_token_response(user.id, db)
    await db.commit()
    return response


@router.post("/refresh", response_model=TokenResponse)
async def refresh(data: RefreshRequest, db: AsyncSession = Depends(get_db)):
    token_hash = hash_token(data.refresh_token)

    result = await db.execute(
        select(RefreshToken).where(RefreshToken.token_hash == token_hash)
    )
    stored_token = result.scalar_one_or_none()

    if (
        not stored_token
        or stored_token.is_revoked
        or stored_token.expires_at < datetime.now(UTC)
    ):
        raise UnauthorizedError("Invalid or expired refresh token")

    stored_token.is_revoked = True

    response = _prepare_token_response(stored_token.user_id, db)
    await db.commit()
    return response


@router.post("/logout", status_code=204)
async def logout(data: RefreshRequest, db: AsyncSession = Depends(get_db)):
    token_hash = hash_token(data.refresh_token)

    result = await db.execute(
        select(RefreshToken).where(RefreshToken.token_hash == token_hash)
    )
    stored_token = result.scalar_one_or_none()

    if stored_token:
        stored_token.is_revoked = True
        await db.commit()
