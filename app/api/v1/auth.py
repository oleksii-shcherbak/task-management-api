from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

from fastapi import APIRouter, Depends
from fastapi.responses import RedirectResponse
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.config import settings
from app.core.arq_pool import get_arq_pool
from app.core.exceptions import (
    ConflictError,
    NotFoundError,
    UnauthorizedError,
    ValidationError,
)
from app.core.rate_limit import RateLimiter
from app.core.security import (
    create_access_token,
    generate_refresh_token,
    hash_password,
    hash_token,
    verify_password,
)
from app.database import get_db
from app.models.email_verification_token import EmailVerificationToken
from app.models.oauth_account import OAuthAccount, OAuthProvider
from app.models.password_reset_token import PasswordResetToken
from app.models.refresh_token import RefreshToken
from app.models.user import User
from app.schemas.auth import (
    ForgotPasswordRequest,
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    ResetPasswordRequest,
    SetPasswordRequest,
    TokenResponse,
)
from app.services.github import exchange_code_for_token, fetch_github_profile

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
async def register(
    data: RegisterRequest,
    db: AsyncSession = Depends(get_db),
    arq_pool=Depends(get_arq_pool),
):
    result = await db.execute(
        select(User).where(User.email == data.email, User.deleted_at.is_(None))
    )
    if result.scalar_one_or_none():
        raise ConflictError("Email already registered")

    user = User(
        email=data.email,
        password_hash=hash_password(data.password),
        name=data.name,
        is_active=True,
        password_changed_at=datetime.now(UTC),
    )
    db.add(user)
    await db.flush()

    response = _prepare_token_response(user.id, db)

    plain_verification_token = generate_refresh_token()
    db.add(
        EmailVerificationToken(
            token_hash=hash_token(plain_verification_token),
            user_id=user.id,
            expires_at=datetime.now(UTC) + timedelta(hours=24),
        )
    )

    await db.commit()
    await arq_pool.enqueue_job(
        "send_verification_email",
        user_id=user.id,
        token=plain_verification_token,
    )
    return response


@router.post(
    "/login",
    response_model=TokenResponse,
    dependencies=[Depends(RateLimiter(limit=5, window=60))],
)
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


@router.get("/verify-email")
async def verify_email(token: str, db: AsyncSession = Depends(get_db)):
    token_hash = hash_token(token)

    result = await db.execute(
        select(EmailVerificationToken).where(
            EmailVerificationToken.token_hash == token_hash
        )
    )
    record = result.scalar_one_or_none()

    if (
        not record
        or record.used_at is not None
        or record.expires_at < datetime.now(UTC)
    ):
        raise NotFoundError("Invalid or expired verification token")

    record.used_at = datetime.now(UTC)

    result = await db.execute(select(User).where(User.id == record.user_id))
    user = result.scalar_one_or_none()
    if user:
        user.is_verified = True

    await db.commit()
    return {"message": "Email verified successfully"}


@router.post(
    "/resend-verification",
    dependencies=[Depends(RateLimiter(limit=3, window=3600))],
)
async def resend_verification(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    arq_pool=Depends(get_arq_pool),
):
    if current_user.is_verified:
        raise ConflictError("Email is already verified")

    # Consume any outstanding unused tokens so only one is valid at a time
    await db.execute(
        update(EmailVerificationToken)
        .where(
            EmailVerificationToken.user_id == current_user.id,
            EmailVerificationToken.used_at.is_(None),
        )
        .values(used_at=datetime.now(UTC))
    )

    plain_token = generate_refresh_token()
    db.add(
        EmailVerificationToken(
            token_hash=hash_token(plain_token),
            user_id=current_user.id,
            expires_at=datetime.now(UTC) + timedelta(hours=24),
        )
    )
    await db.commit()
    await arq_pool.enqueue_job(
        "send_verification_email",
        user_id=current_user.id,
        token=plain_token,
    )
    return {"message": "Verification email sent"}


@router.get("/github")
async def github_oauth_redirect():
    params = urlencode(
        {
            "client_id": settings.GITHUB_CLIENT_ID,
            "redirect_uri": settings.GITHUB_REDIRECT_URI,
            "scope": "user:email",
        }
    )
    return RedirectResponse(f"https://github.com/login/oauth/authorize?{params}")


@router.get("/github/callback", response_model=TokenResponse)
async def github_oauth_callback(code: str, db: AsyncSession = Depends(get_db)):
    github_token = await exchange_code_for_token(
        code,
        settings.GITHUB_CLIENT_ID,
        settings.GITHUB_CLIENT_SECRET,
        settings.GITHUB_REDIRECT_URI,
    )
    profile = await fetch_github_profile(github_token)

    github_id = str(profile["id"])
    email = profile.get("email")
    name = profile.get("name") or profile.get("login") or email

    if not email:
        raise ValidationError("GitHub account has no accessible email address")

    result = await db.execute(
        select(OAuthAccount).where(
            OAuthAccount.provider == OAuthProvider.GITHUB,
            OAuthAccount.provider_user_id == github_id,
        )
    )
    oauth_account = result.scalar_one_or_none()

    if oauth_account:
        oauth_account.access_token = github_token
        response = _prepare_token_response(oauth_account.user_id, db)
        await db.commit()
        return response

    result = await db.execute(
        select(User).where(User.email == email, User.deleted_at.is_(None))
    )
    user = result.scalar_one_or_none()

    if not user:
        user = User(
            email=email,
            name=name,
            is_active=True,
            is_verified=True,
            password_changed_at=datetime.now(UTC),
        )
        db.add(user)
        await db.flush()
    else:
        user.is_verified = True

    db.add(
        OAuthAccount(
            user_id=user.id,
            provider=OAuthProvider.GITHUB,
            provider_user_id=github_id,
            provider_email=email,
            access_token=github_token,
        )
    )
    response = _prepare_token_response(user.id, db)
    await db.commit()
    return response


@router.post("/set-password", status_code=204)
async def set_password(
    data: SetPasswordRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if current_user.password_hash is not None:
        raise ConflictError(
            "Account already has a password - use change-password instead"
        )

    current_user.password_hash = hash_password(data.password)
    current_user.password_changed_at = datetime.now(UTC)

    await db.execute(
        update(RefreshToken)
        .where(
            RefreshToken.user_id == current_user.id, RefreshToken.is_revoked.is_(False)
        )
        .values(is_revoked=True)
    )
    await db.commit()


@router.post(
    "/forgot-password",
    dependencies=[Depends(RateLimiter(limit=3, window=3600))],
)
async def forgot_password(
    data: ForgotPasswordRequest,
    db: AsyncSession = Depends(get_db),
    arq_pool=Depends(get_arq_pool),
):
    result = await db.execute(
        select(User).where(User.email == data.email, User.deleted_at.is_(None))
    )
    user = result.scalar_one_or_none()

    if user is None:
        # Don't reveal whether the email exists to prevent user enumeration attacks
        return {
            "message": "If an account with that email exists, a reset link has been sent."
        }

    # Consume any outstanding unused tokens so only one is valid at a time
    await db.execute(
        update(PasswordResetToken)
        .where(
            PasswordResetToken.user_id == user.id,
            PasswordResetToken.used_at.is_(None),
        )
        .values(used_at=datetime.now(UTC))
    )

    plain_token = generate_refresh_token()
    db.add(
        PasswordResetToken(
            token_hash=hash_token(plain_token),
            user_id=user.id,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    await db.commit()
    await arq_pool.enqueue_job(
        "send_password_reset_email",
        user_id=user.id,
        token=plain_token,
    )
    return {
        "message": "If an account with that email exists, a reset link has been sent."
    }


@router.post("/reset-password", status_code=204)
async def reset_password(
    data: ResetPasswordRequest, db: AsyncSession = Depends(get_db)
):
    token_hash = hash_token(data.token)

    result = await db.execute(
        select(PasswordResetToken).where(PasswordResetToken.token_hash == token_hash)
    )
    record = result.scalar_one_or_none()

    if (
        not record
        or record.used_at is not None
        or record.expires_at < datetime.now(UTC)
    ):
        raise NotFoundError("Invalid or expired reset token")

    record.used_at = datetime.now(UTC)

    result = await db.execute(select(User).where(User.id == record.user_id))
    user = result.scalar_one_or_none()
    if user:
        user.password_hash = hash_password(data.password)
        user.password_changed_at = datetime.now(UTC)

    await db.execute(
        update(RefreshToken)
        .where(
            RefreshToken.user_id == record.user_id, RefreshToken.is_revoked.is_(False)
        )
        .values(is_revoked=True)
    )
    await db.commit()
