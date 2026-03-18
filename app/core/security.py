import hashlib
import secrets
from datetime import UTC, datetime, timedelta

import bcrypt
import jwt

from app.config import settings


def hash_password(plain_password: str) -> str:
    """
    Hash a plain-text password using bcrypt.

    Args:
        plain_password: The plain-text password to hash

    Returns:
        The hashed password as a string (ready for database storage)
    """
    return bcrypt.hashpw(
        plain_password.encode("utf-8"), bcrypt.gensalt(rounds=12)
    ).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str | None) -> bool:
    if hashed_password is None:
        return False
    try:
        return bcrypt.checkpw(
            plain_password.encode("utf-8"), hashed_password.encode("utf-8")
        )
    except ValueError:
        return False


def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    """
    Create JWT access token.

    Args:
        data: Payload to encode
        expires_delta: Optional custom expiration (defaults to config value)
    """
    to_encode = data.copy()

    # Use provided expires_delta, or default to config
    if expires_delta is None:
        expires_delta = timedelta(minutes=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES)

    expire = datetime.now(UTC) + expires_delta
    to_encode.update({"exp": expire, "iat": datetime.now(UTC)})

    # Always use config for secret_key and algorithm
    return jwt.encode(
        to_encode, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM
    )


def decode_access_token(token: str) -> dict:
    """
    Decode and validate JWT access token.

    Args:
        token: The JWT token to decode

    Returns:
        The decoded payload as a dictionary

    Raises:
        jwt.ExpiredSignatureError: If the token has expired
        jwt.InvalidTokenError: If the token is invalid
    """
    return jwt.decode(
        token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM]
    )


def generate_refresh_token() -> str:
    """
    Generate a cryptographically secure random token string.

    Returns:
        A URL-safe random token (43 characters, 256 bits of entropy)
    """
    return secrets.token_urlsafe(32)


def create_state_token() -> str:
    """Short-lived JWT used as the OAuth `state` parameter for CSRF protection."""
    return create_access_token(
        {"type": "oauth_state"}, expires_delta=timedelta(minutes=10)
    )


def verify_state_token(state: str) -> bool:
    """Return True if the state value is a valid, unexpired OAuth state JWT."""
    try:
        payload = decode_access_token(state)
        return payload.get("type") == "oauth_state"
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
        return False


def hash_token(plain_token: str) -> str:
    """
    Hash a plain token using SHA-256 for database storage.

    Args:
        plain_token: The plain-text token to hash

    Returns:
        A 64-character hex digest suitable for the token_hash column
    """
    return hashlib.sha256(plain_token.encode()).hexdigest()
