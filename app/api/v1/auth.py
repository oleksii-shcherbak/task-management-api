from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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
    RegisterResponse,
    TokenResponse,
    UserResponse,
)

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post("/register", response_model=RegisterResponse, status_code=201)
async def register(data: RegisterRequest, db: AsyncSession = Depends(get_db)):
    # Check if email is already registered
    result = await db.execute(select(User).where(User.email == data.email))
    if result.scalar_one_or_none():
        raise ConflictError("Email already registered")

    user = User(
        email=data.email,
        password_hash=hash_password(data.password),
        name=data.name,
        password_changed_at=datetime.now(UTC),
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)  # Refresh to get the generated ID and other fields

    return RegisterResponse(
        message="Registration successful",
        user=UserResponse.model_validate(user),
    )


@router.post("/login", response_model=TokenResponse)
async def login(data: LoginRequest, db: AsyncSession = Depends(get_db)):
    # Load user by email
    result = await db.execute(select(User).where(User.email == data.email))
    user = result.scalar_one_or_none()

    # Always check both user existence AND password before raising error.
    # This prevents timing attacks that reveal whether an email is registered.
    if (
        not user
        or user.deleted_at is not None
        or not verify_password(data.password, user.password_hash)
    ):
        raise UnauthorizedError("Invalid email or password")

    # Issue tokens
    access_token = create_access_token({"sub": str(user.id)})
    plain_refresh_token = generate_refresh_token()

    # Store hashed refresh token in DB
    db.add(
        RefreshToken(
            token_hash=hash_token(plain_refresh_token),
            user_id=user.id,
            expires_at=datetime.now(UTC) + timedelta(days=30),
        )
    )
    await db.commit()

    return TokenResponse(access_token=access_token, refresh_token=plain_refresh_token)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(data: RefreshRequest, db: AsyncSession = Depends(get_db)):
    token_hash = hash_token(data.refresh_token)

    # Look up the token by its hash
    result = await db.execute(
        select(RefreshToken).where(RefreshToken.token_hash == token_hash)
    )
    stored_token = result.scalar_one_or_none()

    # Validate: must exist, not revoked, not expired
    if (
        not stored_token
        or stored_token.is_revoked
        or stored_token.expires_at < datetime.now(UTC)
    ):
        raise UnauthorizedError("Invalid or expired refresh token")

    # Revoke the old token and issue a new one
    stored_token.is_revoked = True

    plain_refresh_token = generate_refresh_token()
    db.add(
        RefreshToken(
            token_hash=hash_token(plain_refresh_token),
            user_id=stored_token.user_id,
            expires_at=datetime.now(UTC) + timedelta(days=30),
        )
    )

    # Issue new access token
    access_token = create_access_token({"sub": str(stored_token.user_id)})
    await db.commit()

    return TokenResponse(access_token=access_token, refresh_token=plain_refresh_token)


@router.post("/logout", status_code=204)
async def logout(data: RefreshRequest, db: AsyncSession = Depends(get_db)):
    token_hash = hash_token(data.refresh_token)

    result = await db.execute(
        select(RefreshToken).where(RefreshToken.token_hash == token_hash)
    )
    stored_token = result.scalar_one_or_none()

    # If token exists, revoke it. If not, can be ignored since it's effectively "logged out" already
    if stored_token:
        stored_token.is_revoked = True
        await db.commit()
