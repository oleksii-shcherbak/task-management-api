import re
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import structlog
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

logger = structlog.get_logger()

_SLUG_STRIP_RE = re.compile(r"[^a-z0-9_-]")


def _slugify_name(name: str) -> str:
    """Convert a display name to a valid username slug (may still need a uniqueness suffix)."""
    slug = _SLUG_STRIP_RE.sub("_", name.lower()).strip("_-")
    return slug[:25] or "user"


async def _resolve_username(base: str, user_id: int, db: AsyncSession) -> str:
    """Return base slug if unclaimed, otherwise base_{user_id} which is always unique."""
    result = await db.execute(
        select(User.id).where(User.username == base, User.deleted_at.is_(None))
    )
    taken_by = result.scalar_one_or_none()
    if taken_by is None or taken_by == user_id:
        return base
    return f"{base}_{user_id}"


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


async def _issue_verification_email(user_id: int, db: AsyncSession, arq_pool) -> None:
    """Add a fresh email verification token to the session, commit, and enqueue the send job."""
    plain_token = generate_refresh_token()
    db.add(
        EmailVerificationToken(
            token_hash=hash_token(plain_token),
            user_id=user_id,
            expires_at=datetime.now(UTC) + timedelta(hours=24),
        )
    )
    await db.commit()
    await arq_pool.enqueue_job(
        "send_verification_email", user_id=user_id, token=plain_token
    )


async def _revoke_all_refresh_tokens(user_id: int, db: AsyncSession) -> None:
    """Mark all active refresh tokens for a user as revoked (caller must commit)."""
    await db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id, RefreshToken.is_revoked.is_(False))
        .values(is_revoked=True)
    )


@router.post(
    "/register",
    response_model=TokenResponse,
    status_code=201,
    summary="Register a new user",
    description="Create an account and return tokens immediately - no separate login step needed. A verification email is queued in the background.",
    responses={
        409: {"description": "Email or username already taken"},
        422: {"description": "Validation error"},
    },
)
async def register(
    data: RegisterRequest,
    db: AsyncSession = Depends(get_db),
    arq_pool=Depends(get_arq_pool),
) -> TokenResponse:
    result = await db.execute(
        select(User).where(User.email == data.email, User.deleted_at.is_(None))
    )
    if result.scalar_one_or_none():
        raise ConflictError("Email already registered")

    if data.username:
        taken = await db.execute(
            select(User.id).where(
                User.username == data.username, User.deleted_at.is_(None)
            )
        )
        if taken.scalar_one_or_none():
            raise ConflictError("Username already taken")

    user = User(
        email=data.email,
        password_hash=hash_password(data.password),
        name=data.name,
        # Temporary placeholder - replaced below after flush gives the id
        username="_",
        is_active=True,
        password_changed_at=datetime.now(UTC),
    )
    db.add(user)
    await db.flush()

    if data.username:
        user.username = data.username
    else:
        base = _slugify_name(data.name)
        user.username = await _resolve_username(base, user.id, db)

    response = _prepare_token_response(user.id, db)
    await _issue_verification_email(user.id, db, arq_pool)
    return response


@router.post(
    "/login",
    response_model=TokenResponse,
    dependencies=[Depends(RateLimiter(limit=5, window=60))],
    summary="Log in",
    description="Authenticate with email or username plus password. Returns an access token (15 min) and a refresh token (30 days).",
    responses={
        401: {"description": "Invalid credentials"},
        422: {"description": "Validation error"},
        429: {"description": "Rate limit exceeded"},
    },
)
async def login(
    data: LoginRequest, db: AsyncSession = Depends(get_db)
) -> TokenResponse:
    if "@" in data.identifier:
        condition = User.email == data.identifier
    else:
        condition = User.username == data.identifier

    result = await db.execute(select(User).where(condition, User.deleted_at.is_(None)))
    user: User | None = result.scalar_one_or_none()

    if not user or not verify_password(data.password, user.password_hash):
        identifier_type = "email" if "@" in data.identifier else "username"
        logger.warning("login_failed", identifier_type=identifier_type)
        raise UnauthorizedError("Invalid credentials")

    response = _prepare_token_response(user.id, db)
    await db.commit()
    return response


@router.post(
    "/refresh",
    response_model=TokenResponse,
    summary="Refresh access token",
    description="Exchange a valid refresh token for a new token pair. The submitted token is immediately revoked (rotation).",
    responses={
        401: {"description": "Invalid or expired refresh token"},
        422: {"description": "Validation error"},
    },
)
async def refresh(
    data: RefreshRequest, db: AsyncSession = Depends(get_db)
) -> TokenResponse:
    token_hash = hash_token(data.refresh_token)

    result = await db.execute(
        select(RefreshToken).where(RefreshToken.token_hash == token_hash)
    )
    stored_token: RefreshToken | None = result.scalar_one_or_none()

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


@router.post(
    "/logout",
    status_code=204,
    summary="Log out",
    description="Revoke a refresh token. Silently succeeds if the token is unknown or already revoked.",
    responses={422: {"description": "Validation error"}},
)
async def logout(data: RefreshRequest, db: AsyncSession = Depends(get_db)) -> None:
    token_hash = hash_token(data.refresh_token)

    result = await db.execute(
        select(RefreshToken).where(RefreshToken.token_hash == token_hash)
    )
    stored_token: RefreshToken | None = result.scalar_one_or_none()

    if stored_token:
        stored_token.is_revoked = True
        await db.commit()


@router.get(
    "/verify-email",
    summary="Verify email address",
    description="Consume a one-time email verification token and mark the account as verified.",
    responses={
        404: {"description": "Invalid or expired token"},
        422: {"description": "Validation error"},
    },
)
async def verify_email(
    token: str, db: AsyncSession = Depends(get_db)
) -> dict[str, str]:
    token_hash = hash_token(token)

    result = await db.execute(
        select(EmailVerificationToken).where(
            EmailVerificationToken.token_hash == token_hash
        )
    )
    record: EmailVerificationToken | None = result.scalar_one_or_none()

    if (
        not record
        or record.used_at is not None
        or record.expires_at < datetime.now(UTC)
    ):
        raise NotFoundError("Invalid or expired verification token")

    record.used_at = datetime.now(UTC)

    result = await db.execute(select(User).where(User.id == record.user_id))
    verification_user: User | None = result.scalar_one_or_none()
    if verification_user:
        verification_user.is_verified = True

    await db.commit()
    return {"message": "Email verified successfully"}


@router.post(
    "/resend-verification",
    dependencies=[Depends(RateLimiter(limit=3, window=3600))],
    summary="Resend verification email",
    description="Queue a fresh verification email. Any previously unused tokens for this account are invalidated.",
    responses={
        401: {"description": "Not authenticated"},
        409: {"description": "Email is already verified"},
        429: {"description": "Rate limit exceeded"},
    },
)
async def resend_verification(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    arq_pool=Depends(get_arq_pool),
) -> dict[str, str]:
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

    await _issue_verification_email(current_user.id, db, arq_pool)
    return {"message": "Verification email sent"}


@router.get(
    "/github",
    summary="Start GitHub OAuth flow",
    description="Redirect the browser to GitHub's authorization page to begin the OAuth login flow.",
)
async def github_oauth_redirect() -> RedirectResponse:
    params = urlencode(
        {
            "client_id": settings.GITHUB_CLIENT_ID,
            "redirect_uri": settings.GITHUB_REDIRECT_URI,
            "scope": "user:email",
        }
    )
    return RedirectResponse(f"https://github.com/login/oauth/authorize?{params}")


@router.get(
    "/github/callback",
    response_model=TokenResponse,
    summary="GitHub OAuth callback",
    description="Exchange the authorization code for tokens. Creates a new account if no matching GitHub or email record exists.",
    responses={
        401: {"description": "GitHub OAuth failed"},
        422: {"description": "GitHub account has no accessible email"},
    },
)
async def github_oauth_callback(
    code: str, db: AsyncSession = Depends(get_db)
) -> TokenResponse:
    github_token = await exchange_code_for_token(
        code,
        settings.GITHUB_CLIENT_ID,
        settings.GITHUB_CLIENT_SECRET,
        settings.GITHUB_REDIRECT_URI,
    )
    profile = await fetch_github_profile(github_token)

    github_id = str(profile["id"])
    email: str | None = profile.get("email")

    if not email:
        raise ValidationError("GitHub account has no accessible email address")

    name: str = profile.get("name") or profile.get("login") or email

    result = await db.execute(
        select(OAuthAccount).where(
            OAuthAccount.provider == OAuthProvider.GITHUB,
            OAuthAccount.provider_user_id == github_id,
        )
    )
    oauth_account: OAuthAccount | None = result.scalar_one_or_none()

    if oauth_account:
        oauth_account.access_token = github_token
        response = _prepare_token_response(oauth_account.user_id, db)
        await db.commit()
        return response

    result = await db.execute(
        select(User).where(User.email == email, User.deleted_at.is_(None))
    )
    user: User | None = result.scalar_one_or_none()

    if not user:
        resolved_user = User(
            email=email,
            name=name,
            username="_",
            is_active=True,
            is_verified=True,
            password_changed_at=datetime.now(UTC),
        )
        db.add(resolved_user)
        await db.flush()
        base = _slugify_name(name)
        resolved_user.username = await _resolve_username(
            base, int(resolved_user.id), db
        )
    else:
        resolved_user = user
        resolved_user.is_verified = True

    db.add(
        OAuthAccount(
            user_id=int(resolved_user.id),
            provider=OAuthProvider.GITHUB,
            provider_user_id=github_id,
            provider_email=email,
            access_token=github_token,
        )
    )
    response = _prepare_token_response(int(resolved_user.id), db)
    await db.commit()
    return response


@router.post(
    "/set-password",
    status_code=204,
    summary="Set password for OAuth account",
    description="Add a password to an account that was created via OAuth and currently has none.",
    responses={
        401: {"description": "Not authenticated"},
        409: {"description": "Account already has a password"},
        422: {"description": "Validation error"},
    },
)
async def set_password(
    data: SetPasswordRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    if current_user.password_hash is not None:
        raise ConflictError(
            "Account already has a password - use change-password instead"
        )

    current_user.password_hash = hash_password(data.password)
    current_user.password_changed_at = datetime.now(UTC)
    await _revoke_all_refresh_tokens(int(current_user.id), db)
    await db.commit()


@router.post(
    "/forgot-password",
    dependencies=[Depends(RateLimiter(limit=3, window=3600))],
    summary="Request password reset",
    description="Send a reset link if the email belongs to an active account. The response is identical whether the email exists or not to prevent enumeration.",
    responses={
        422: {"description": "Validation error"},
        429: {"description": "Rate limit exceeded"},
    },
)
async def forgot_password(
    data: ForgotPasswordRequest,
    db: AsyncSession = Depends(get_db),
    arq_pool=Depends(get_arq_pool),
) -> dict[str, str]:
    result = await db.execute(
        select(User).where(User.email == data.email, User.deleted_at.is_(None))
    )
    user: User | None = result.scalar_one_or_none()

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


@router.post(
    "/reset-password",
    status_code=204,
    summary="Reset password",
    description="Consume a one-time reset token and set a new password. All active refresh tokens are revoked.",
    responses={
        404: {"description": "Invalid or expired reset token"},
        422: {"description": "Validation error"},
    },
)
async def reset_password(
    data: ResetPasswordRequest, db: AsyncSession = Depends(get_db)
) -> None:
    token_hash = hash_token(data.token)

    result = await db.execute(
        select(PasswordResetToken).where(PasswordResetToken.token_hash == token_hash)
    )
    record: PasswordResetToken | None = result.scalar_one_or_none()

    if (
        not record
        or record.used_at is not None
        or record.expires_at < datetime.now(UTC)
    ):
        raise NotFoundError("Invalid or expired reset token")

    record.used_at = datetime.now(UTC)

    result = await db.execute(select(User).where(User.id == record.user_id))
    reset_user: User | None = result.scalar_one_or_none()
    if reset_user:
        reset_user.password_hash = hash_password(data.password)
        reset_user.password_changed_at = datetime.now(UTC)
    await _revoke_all_refresh_tokens(record.user_id, db)
    await db.commit()
