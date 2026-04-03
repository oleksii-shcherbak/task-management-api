from datetime import UTC, datetime, timedelta
from unittest.mock import ANY, AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import UnauthorizedError
from app.core.security import hash_token
from app.models.email_verification_token import EmailVerificationToken

VALID_USER = {
    "email": "alice@example.com",
    "password": "securepassword123",
    "name": "Alice",
}


async def register_user(client: AsyncClient, user: dict | None = None) -> dict:
    resolved = user if user is not None else VALID_USER
    response = await client.post("/api/v1/auth/register", json=resolved)
    assert response.status_code == 201, f"Registration failed: {response.text}"
    return response.json()


async def login_user(client: AsyncClient, user: dict | None = None) -> dict:
    resolved = user if user is not None else VALID_USER
    response = await client.post(
        "/api/v1/auth/login",
        json={"identifier": resolved["email"], "password": resolved["password"]},
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
async def test_register_user_is_active(client: AsyncClient):
    tokens = await register_user(client)

    me = await client.get(
        "/api/v1/users/me",
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert me.json()["is_active"] is True


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


@pytest.mark.asyncio
async def test_register_enqueues_verification_email(client: AsyncClient, arq_mock):
    await client.post("/api/v1/auth/register", json=VALID_USER)

    arq_mock.enqueue_job.assert_called_once_with(
        "send_verification_email",
        user_id=ANY,
        token=ANY,
    )


# --- Login ---


@pytest.mark.asyncio
async def test_login_success(client: AsyncClient):
    await register_user(client)
    response = await client.post(
        "/api/v1/auth/login",
        json={"identifier": VALID_USER["email"], "password": VALID_USER["password"]},
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
        json={"identifier": VALID_USER["email"], "password": "wrongpassword"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_login_nonexistent_email_returns_401(client: AsyncClient):
    response = await client.post(
        "/api/v1/auth/login",
        json={"identifier": "nobody@example.com", "password": "password123"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_login_error_message_is_generic(client: AsyncClient):
    await register_user(client)

    wrong_password = await client.post(
        "/api/v1/auth/login",
        json={"identifier": VALID_USER["email"], "password": "wrongpassword"},
    )
    no_user = await client.post(
        "/api/v1/auth/login",
        json={"identifier": "ghost@example.com", "password": "doesntmatter"},
    )

    assert (
        wrong_password.json()["error"]["message"] == no_user.json()["error"]["message"]
    )


@pytest.mark.asyncio
async def test_register_with_explicit_username(client: AsyncClient):
    tokens = await client.post(
        "/api/v1/auth/register", json={**VALID_USER, "username": "alice_dev"}
    )
    assert tokens.status_code == 201
    me = await client.get(
        "/api/v1/users/me",
        headers={"Authorization": f"Bearer {tokens.json()['access_token']}"},
    )
    assert me.json()["username"] == "alice_dev"


@pytest.mark.asyncio
async def test_register_auto_generates_username_when_absent(client: AsyncClient):
    tokens = await register_user(client)
    me = await client.get(
        "/api/v1/users/me",
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert me.json()["username"] != ""


@pytest.mark.asyncio
async def test_register_duplicate_username_returns_409(client: AsyncClient):
    await client.post("/api/v1/auth/register", json={**VALID_USER, "username": "taken"})
    response = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "other@example.com",
            "password": "securepass123",
            "name": "Other",
            "username": "taken",
        },
    )
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_register_reserved_username_returns_422(client: AsyncClient):
    response = await client.post(
        "/api/v1/auth/register", json={**VALID_USER, "username": "admin"}
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_register_invalid_username_format_returns_422(client: AsyncClient):
    response = await client.post(
        "/api/v1/auth/register", json={**VALID_USER, "username": "UPPERCASE"}
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_login_by_username(client: AsyncClient):
    tokens = await client.post(
        "/api/v1/auth/register", json={**VALID_USER, "username": "alice_dev"}
    )
    access = tokens.json()["access_token"]
    me = (
        await client.get(
            "/api/v1/users/me", headers={"Authorization": f"Bearer {access}"}
        )
    ).json()

    response = await client.post(
        "/api/v1/auth/login",
        json={"identifier": me["username"], "password": VALID_USER["password"]},
    )
    assert response.status_code == 200


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


@pytest.mark.asyncio
async def test_resend_verification_enqueues_email(client: AsyncClient, arq_mock):
    tokens = await register_user(client)
    arq_mock.enqueue_job.reset_mock()

    await client.post(
        "/api/v1/auth/resend-verification",
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )

    arq_mock.enqueue_job.assert_called_once_with(
        "send_verification_email",
        user_id=ANY,
        token=ANY,
    )


# --- GitHub OAuth ---


GITHUB_PROFILE = {
    "id": 12345,
    "login": "octocat",
    "name": "The Octocat",
    "email": "octocat@github.com",
}


@pytest.mark.asyncio
async def test_github_redirect_returns_302(client: AsyncClient):
    response = await client.get("/api/v1/auth/github", follow_redirects=False)
    assert response.status_code in (302, 307)
    assert "github.com/login/oauth/authorize" in response.headers["location"]


@pytest.mark.asyncio
async def test_github_callback_new_user_returns_tokens(client: AsyncClient):
    with (
        patch(
            "app.api.v1.auth.exchange_code_for_token",
            new=AsyncMock(return_value="gh_token"),
        ),
        patch(
            "app.api.v1.auth.fetch_github_profile",
            new=AsyncMock(return_value=GITHUB_PROFILE),
        ),
    ):
        response = await client.get("/api/v1/auth/github/callback?code=testcode")

    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert "refresh_token" in data


@pytest.mark.asyncio
async def test_github_callback_new_user_is_verified(client: AsyncClient):
    with (
        patch(
            "app.api.v1.auth.exchange_code_for_token",
            new=AsyncMock(return_value="gh_token"),
        ),
        patch(
            "app.api.v1.auth.fetch_github_profile",
            new=AsyncMock(return_value=GITHUB_PROFILE),
        ),
    ):
        tokens = (await client.get("/api/v1/auth/github/callback?code=testcode")).json()

    me = await client.get(
        "/api/v1/users/me",
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert me.json()["is_verified"] is True


@pytest.mark.asyncio
async def test_github_callback_existing_account_logs_in(client: AsyncClient):
    with (
        patch(
            "app.api.v1.auth.exchange_code_for_token",
            new=AsyncMock(return_value="gh_token"),
        ),
        patch(
            "app.api.v1.auth.fetch_github_profile",
            new=AsyncMock(return_value=GITHUB_PROFILE),
        ),
    ):
        first = await client.get("/api/v1/auth/github/callback?code=code1")
    assert first.status_code == 200

    with (
        patch(
            "app.api.v1.auth.exchange_code_for_token",
            new=AsyncMock(return_value="gh_token2"),
        ),
        patch(
            "app.api.v1.auth.fetch_github_profile",
            new=AsyncMock(return_value=GITHUB_PROFILE),
        ),
    ):
        second = await client.get("/api/v1/auth/github/callback?code=code2")
    assert second.status_code == 200


@pytest.mark.asyncio
async def test_github_callback_links_existing_email_user(client: AsyncClient):
    await register_user(client, {**VALID_USER, "email": GITHUB_PROFILE["email"]})

    with (
        patch(
            "app.api.v1.auth.exchange_code_for_token",
            new=AsyncMock(return_value="gh_token"),
        ),
        patch(
            "app.api.v1.auth.fetch_github_profile",
            new=AsyncMock(return_value=GITHUB_PROFILE),
        ),
    ):
        response = await client.get("/api/v1/auth/github/callback?code=testcode")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_github_callback_links_existing_user_sets_verified(client: AsyncClient):
    tokens = await register_user(
        client, {**VALID_USER, "email": GITHUB_PROFILE["email"]}
    )
    me_before = await client.get(
        "/api/v1/users/me",
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert me_before.json()["is_verified"] is False

    with (
        patch(
            "app.api.v1.auth.exchange_code_for_token",
            new=AsyncMock(return_value="gh_token"),
        ),
        patch(
            "app.api.v1.auth.fetch_github_profile",
            new=AsyncMock(return_value=GITHUB_PROFILE),
        ),
    ):
        oauth_tokens = (
            await client.get("/api/v1/auth/github/callback?code=testcode")
        ).json()

    me_after = await client.get(
        "/api/v1/users/me",
        headers={"Authorization": f"Bearer {oauth_tokens['access_token']}"},
    )
    assert me_after.json()["is_verified"] is True


@pytest.mark.asyncio
async def test_github_callback_invalid_code_returns_401(client: AsyncClient):
    with patch(
        "app.api.v1.auth.exchange_code_for_token",
        new=AsyncMock(side_effect=UnauthorizedError("invalid code")),
    ):
        response = await client.get("/api/v1/auth/github/callback?code=badcode")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_github_callback_no_email_returns_422(client: AsyncClient):
    with (
        patch(
            "app.api.v1.auth.exchange_code_for_token",
            new=AsyncMock(return_value="gh_token"),
        ),
        patch(
            "app.api.v1.auth.fetch_github_profile",
            new=AsyncMock(return_value={**GITHUB_PROFILE, "email": None}),
        ),
    ):
        response = await client.get("/api/v1/auth/github/callback?code=testcode")
    assert response.status_code == 422


# --- Set Password ---


@pytest.mark.asyncio
async def test_set_password_success(client: AsyncClient):
    with (
        patch(
            "app.api.v1.auth.exchange_code_for_token",
            new=AsyncMock(return_value="gh_token"),
        ),
        patch(
            "app.api.v1.auth.fetch_github_profile",
            new=AsyncMock(return_value=GITHUB_PROFILE),
        ),
    ):
        tokens = (await client.get("/api/v1/auth/github/callback?code=testcode")).json()

    response = await client.post(
        "/api/v1/auth/set-password",
        json={"password": "newpassword123"},
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert response.status_code == 204


@pytest.mark.asyncio
async def test_set_password_allows_subsequent_email_login(client: AsyncClient):
    with (
        patch(
            "app.api.v1.auth.exchange_code_for_token",
            new=AsyncMock(return_value="gh_token"),
        ),
        patch(
            "app.api.v1.auth.fetch_github_profile",
            new=AsyncMock(return_value=GITHUB_PROFILE),
        ),
    ):
        tokens = (await client.get("/api/v1/auth/github/callback?code=testcode")).json()

    await client.post(
        "/api/v1/auth/set-password",
        json={"password": "newpassword123"},
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )

    login_response = await client.post(
        "/api/v1/auth/login",
        json={"identifier": GITHUB_PROFILE["email"], "password": "newpassword123"},
    )
    assert login_response.status_code == 200


@pytest.mark.asyncio
async def test_set_password_already_has_password_returns_409(client: AsyncClient):
    tokens = await register_user(client)

    response = await client.post(
        "/api/v1/auth/set-password",
        json={"password": "newpassword123"},
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_set_password_requires_auth(client: AsyncClient):
    response = await client.post(
        "/api/v1/auth/set-password", json={"password": "newpassword123"}
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_set_password_short_password_returns_422(client: AsyncClient):
    with (
        patch(
            "app.api.v1.auth.exchange_code_for_token",
            new=AsyncMock(return_value="gh_token"),
        ),
        patch(
            "app.api.v1.auth.fetch_github_profile",
            new=AsyncMock(return_value=GITHUB_PROFILE),
        ),
    ):
        tokens = (await client.get("/api/v1/auth/github/callback?code=testcode")).json()

    response = await client.post(
        "/api/v1/auth/set-password",
        json={"password": "short"},
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert response.status_code == 422


# --- Rate Limiting ---


@pytest.mark.asyncio
async def test_login_rate_limit_blocks_excess_requests(client: AsyncClient):
    data = {"identifier": "ratelimit@example.com", "password": "wrongpassword"}
    for _ in range(5):
        await client.post("/api/v1/auth/login", json=data)

    response = await client.post("/api/v1/auth/login", json=data)
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "RATE_LIMIT_EXCEEDED"


@pytest.mark.asyncio
async def test_login_rate_limit_includes_retry_after_header(client: AsyncClient):
    data = {"identifier": "ratelimit@example.com", "password": "wrongpassword"}
    for _ in range(5):
        await client.post("/api/v1/auth/login", json=data)

    response = await client.post("/api/v1/auth/login", json=data)
    assert response.status_code == 429
    assert "retry-after" in response.headers
    assert int(response.headers["retry-after"]) > 0


@pytest.mark.asyncio
async def test_resend_verification_rate_limit_blocks_excess_requests(
    client: AsyncClient,
):
    tokens = await register_user(client)
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    for _ in range(3):
        await client.post("/api/v1/auth/resend-verification", headers=headers)

    response = await client.post("/api/v1/auth/resend-verification", headers=headers)
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "RATE_LIMIT_EXCEEDED"


# --- Password Reset ---


@pytest.mark.asyncio
async def test_forgot_password_enqueues_reset_email(client: AsyncClient, arq_mock):
    await register_user(client)
    arq_mock.enqueue_job.reset_mock()

    response = await client.post(
        "/api/v1/auth/forgot-password",
        json={"email": VALID_USER["email"]},
    )

    assert response.status_code == 200
    arq_mock.enqueue_job.assert_called_once_with(
        "send_password_reset_email",
        user_id=ANY,
        token=ANY,
    )


@pytest.mark.asyncio
async def test_forgot_password_unknown_email_returns_200_without_enqueueing(
    client: AsyncClient, arq_mock
):
    response = await client.post(
        "/api/v1/auth/forgot-password",
        json={"email": "nobody@example.com"},
    )

    assert response.status_code == 200
    arq_mock.enqueue_job.assert_not_called()


@pytest.mark.asyncio
async def test_reset_password_success(client: AsyncClient, arq_mock):
    await register_user(client)
    await client.post(
        "/api/v1/auth/forgot-password", json={"email": VALID_USER["email"]}
    )
    token = arq_mock.enqueue_job.call_args.kwargs["token"]

    response = await client.post(
        "/api/v1/auth/reset-password",
        json={"token": token, "password": "newpassword123"},
    )

    assert response.status_code == 204

    login = await client.post(
        "/api/v1/auth/login",
        json={"identifier": VALID_USER["email"], "password": "newpassword123"},
    )
    assert login.status_code == 200


@pytest.mark.asyncio
async def test_reset_password_old_password_rejected_after_reset(
    client: AsyncClient, arq_mock
):
    await register_user(client)
    await client.post(
        "/api/v1/auth/forgot-password", json={"email": VALID_USER["email"]}
    )
    token = arq_mock.enqueue_job.call_args.kwargs["token"]
    await client.post(
        "/api/v1/auth/reset-password",
        json={"token": token, "password": "newpassword123"},
    )

    login = await client.post(
        "/api/v1/auth/login",
        json={"identifier": VALID_USER["email"], "password": VALID_USER["password"]},
    )
    assert login.status_code == 401


@pytest.mark.asyncio
async def test_reset_password_invalid_token_returns_404(client: AsyncClient):
    response = await client.post(
        "/api/v1/auth/reset-password",
        json={"token": "bogustoken", "password": "newpassword123"},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_reset_password_token_cannot_be_reused(client: AsyncClient, arq_mock):
    await register_user(client)
    await client.post(
        "/api/v1/auth/forgot-password", json={"email": VALID_USER["email"]}
    )
    token = arq_mock.enqueue_job.call_args.kwargs["token"]

    await client.post(
        "/api/v1/auth/reset-password",
        json={"token": token, "password": "newpassword123"},
    )
    response = await client.post(
        "/api/v1/auth/reset-password",
        json={"token": token, "password": "anotherpassword123"},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_forgot_password_rate_limit_blocks_excess_requests(client: AsyncClient):
    await register_user(client)

    for _ in range(3):
        await client.post(
            "/api/v1/auth/forgot-password", json={"email": VALID_USER["email"]}
        )

    response = await client.post(
        "/api/v1/auth/forgot-password", json={"email": VALID_USER["email"]}
    )
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "RATE_LIMIT_EXCEEDED"


@pytest.mark.asyncio
async def test_auto_generated_usernames_are_unique(client: AsyncClient):
    tokens = []
    for i in range(3):
        r = await register_user(
            client,
            {
                "email": f"same_name_{i}@example.com",
                "password": "securepassword123",
                "name": "Alice",
            },
        )
        tokens.append(r["access_token"])

    usernames = [
        (
            await client.get(
                "/api/v1/users/me",
                headers={"Authorization": f"Bearer {token}"},
            )
        ).json()["username"]
        for token in tokens
    ]

    assert len(usernames) == len(set(usernames)), (
        f"Duplicate usernames generated: {usernames}"
    )
