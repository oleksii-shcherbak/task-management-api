from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_token
from app.models.email_verification_token import EmailVerificationToken

VALID_USER = {
    "email": "alice@example.com",
    "password": "securepassword123",
    "name": "Alice",
}


async def register_user(client: AsyncClient, user: dict | None = None) -> dict:
    if user is None:
        user = VALID_USER
    response = await client.post("/api/v1/auth/register", json=user)
    assert response.status_code == 201, f"Registration failed: {response.text}"
    return response.json()


async def login_user(client: AsyncClient, user: dict | None = None) -> dict:
    if user is None:
        user = VALID_USER
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": user["email"], "password": user["password"]},
    )
    assert response.status_code == 200, f"Login failed: {response.text}"
    return response.json()


# --- Registration ---


@pytest.mark.asyncio
async def test_register_success(client: AsyncClient):
    response = await client.post("/api/v1/auth/register", json=VALID_USER)

    assert response.status_code == 201
    data = response.json()
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["token_type"] == "bearer"


@pytest.mark.asyncio
async def test_register_token_is_immediately_usable(client: AsyncClient):
    tokens = await register_user(client)

    me = await client.get(
        "/api/v1/users/me",
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert me.status_code == 200
    assert me.json()["email"] == VALID_USER["email"]


@pytest.mark.asyncio
async def test_register_duplicate_email_returns_409(client: AsyncClient):
    await register_user(client)
    response = await client.post("/api/v1/auth/register", json=VALID_USER)
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_register_short_password_returns_422(client: AsyncClient):
    response = await client.post(
        "/api/v1/auth/register",
        json={**VALID_USER, "password": "short"},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_register_invalid_email_returns_422(client: AsyncClient):
    response = await client.post(
        "/api/v1/auth/register",
        json={**VALID_USER, "email": "not-an-email"},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_register_missing_fields_returns_422(client: AsyncClient):
    response = await client.post(
        "/api/v1/auth/register",
        json={"email": "test@example.com"},
    )
    assert response.status_code == 422


# --- Login ---


@pytest.mark.asyncio
async def test_login_success(client: AsyncClient):
    await register_user(client)
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": VALID_USER["email"], "password": VALID_USER["password"]},
    )

    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["token_type"] == "bearer"


@pytest.mark.asyncio
async def test_login_wrong_password_returns_401(client: AsyncClient):
    await register_user(client)
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": VALID_USER["email"], "password": "wrongpassword"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_login_nonexistent_email_returns_401(client: AsyncClient):
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": "nobody@example.com", "password": "password123"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_login_error_message_is_generic(client: AsyncClient):
    await register_user(client)

    wrong_password = await client.post(
        "/api/v1/auth/login",
        json={"email": VALID_USER["email"], "password": "wrongpassword"},
    )
    no_user = await client.post(
        "/api/v1/auth/login",
        json={"email": "ghost@example.com", "password": "doesntmatter"},
    )

    assert (
        wrong_password.json()["error"]["message"] == no_user.json()["error"]["message"]
    )


# --- Refresh ---


@pytest.mark.asyncio
async def test_refresh_returns_new_tokens(client: AsyncClient):
    await register_user(client)
    tokens = await login_user(client)

    response = await client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": tokens["refresh_token"]},
    )

    assert response.status_code == 200
    new_tokens = response.json()
    assert "access_token" in new_tokens
    assert "refresh_token" in new_tokens
    assert new_tokens["refresh_token"] != tokens["refresh_token"]


@pytest.mark.asyncio
async def test_refresh_old_token_rejected_after_rotation(client: AsyncClient):
    await register_user(client)
    tokens = await login_user(client)
    old_token = tokens["refresh_token"]

    await client.post("/api/v1/auth/refresh", json={"refresh_token": old_token})

    response = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": old_token}
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_refresh_invalid_token_returns_401(client: AsyncClient):
    response = await client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": "completely-made-up-token"},
    )
    assert response.status_code == 401


# --- Logout ---


@pytest.mark.asyncio
async def test_logout_revokes_refresh_token(client: AsyncClient):
    await register_user(client)
    tokens = await login_user(client)

    logout = await client.post(
        "/api/v1/auth/logout",
        json={"refresh_token": tokens["refresh_token"]},
    )
    assert logout.status_code == 204

    refresh = await client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": tokens["refresh_token"]},
    )
    assert refresh.status_code == 401


@pytest.mark.asyncio
async def test_logout_is_idempotent(client: AsyncClient):
    response = await client.post(
        "/api/v1/auth/logout",
        json={"refresh_token": "token-that-never-existed"},
    )
    assert response.status_code == 204


@pytest.mark.asyncio
async def test_logout_twice_both_return_204(client: AsyncClient):
    await register_user(client)
    tokens = await login_user(client)

    first = await client.post(
        "/api/v1/auth/logout", json={"refresh_token": tokens["refresh_token"]}
    )
    second = await client.post(
        "/api/v1/auth/logout", json={"refresh_token": tokens["refresh_token"]}
    )

    assert first.status_code == 204
    assert second.status_code == 204


# --- Email Verification ---


@pytest.mark.asyncio
async def test_verify_email_success(client: AsyncClient, db_session: AsyncSession):
    tokens = await register_user(client)
    me = await client.get(
        "/api/v1/users/me",
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    user_id = me.json()["id"]

    plain_token = "validtesttoken123"
    db_session.add(
        EmailVerificationToken(
            token_hash=hash_token(plain_token),
            user_id=user_id,
            expires_at=datetime.now(UTC) + timedelta(hours=24),
        )
    )
    await db_session.commit()

    response = await client.get(f"/api/v1/auth/verify-email?token={plain_token}")

    assert response.status_code == 200
    assert response.json()["message"] == "Email verified successfully"

    me = await client.get(
        "/api/v1/users/me",
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert me.json()["is_verified"] is True


@pytest.mark.asyncio
async def test_verify_email_invalid_token_returns_404(client: AsyncClient):
    response = await client.get("/api/v1/auth/verify-email?token=doesnotexist")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_verify_email_expired_token_returns_404(
    client: AsyncClient, db_session: AsyncSession
):
    tokens = await register_user(client)
    user_id = (
        await client.get(
            "/api/v1/users/me",
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )
    ).json()["id"]

    plain_token = "expiredtoken123"
    db_session.add(
        EmailVerificationToken(
            token_hash=hash_token(plain_token),
            user_id=user_id,
            expires_at=datetime.now(UTC) - timedelta(hours=1),
        )
    )
    await db_session.commit()

    response = await client.get(f"/api/v1/auth/verify-email?token={plain_token}")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_verify_email_already_used_returns_404(
    client: AsyncClient, db_session: AsyncSession
):
    tokens = await register_user(client)
    user_id = (
        await client.get(
            "/api/v1/users/me",
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )
    ).json()["id"]

    plain_token = "alreadyusedtoken"
    db_session.add(
        EmailVerificationToken(
            token_hash=hash_token(plain_token),
            user_id=user_id,
            expires_at=datetime.now(UTC) + timedelta(hours=24),
            used_at=datetime.now(UTC),
        )
    )
    await db_session.commit()

    response = await client.get(f"/api/v1/auth/verify-email?token={plain_token}")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_resend_verification_success(client: AsyncClient):
    tokens = await register_user(client)

    response = await client.post(
        "/api/v1/auth/resend-verification",
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )

    assert response.status_code == 200
    assert response.json()["message"] == "Verification email sent"


@pytest.mark.asyncio
async def test_resend_verification_already_verified_returns_409(
    client: AsyncClient, db_session: AsyncSession
):
    tokens = await register_user(client)
    user_id = (
        await client.get(
            "/api/v1/users/me",
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )
    ).json()["id"]

    plain_token = "directverifytoken"
    db_session.add(
        EmailVerificationToken(
            token_hash=hash_token(plain_token),
            user_id=user_id,
            expires_at=datetime.now(UTC) + timedelta(hours=24),
        )
    )
    await db_session.commit()
    await client.get(f"/api/v1/auth/verify-email?token={plain_token}")

    response = await client.post(
        "/api/v1/auth/resend-verification",
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_resend_verification_requires_auth(client: AsyncClient):
    response = await client.post("/api/v1/auth/resend-verification")
    assert response.status_code == 401
