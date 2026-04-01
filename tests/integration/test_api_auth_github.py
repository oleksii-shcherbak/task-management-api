import pytest
from httpx import AsyncClient

GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_USER_URL = "https://api.github.com/user"
GITHUB_EMAILS_URL = "https://api.github.com/user/emails"

USER_ALICE = {
    "email": "alice@example.com",
    "password": "securepassword123",
    "name": "Alice",
}

GITHUB_PROFILE_ALICE = {
    "id": 11111,
    "login": "alice",
    "name": "Alice",
    "email": "alice@example.com",
}

GITHUB_PROFILE_BOB = {
    "id": 22222,
    "login": "bob",
    "name": "Bob",
    "email": "bob@example.com",
}


def _mock_token(httpx_mock, token: str = "gho_test_token") -> None:
    httpx_mock.add_response(
        url=GITHUB_TOKEN_URL,
        method="POST",
        json={"access_token": token, "token_type": "bearer"},
    )


def _mock_profile(httpx_mock, profile: dict) -> None:
    httpx_mock.add_response(url=GITHUB_USER_URL, json=profile)


# --- Redirect ---


@pytest.mark.asyncio
async def test_github_redirect(client: AsyncClient):
    response = await client.get("/api/v1/auth/github", follow_redirects=False)

    assert response.status_code == 307
    location = response.headers["location"]
    assert "https://github.com/login/oauth/authorize" in location
    assert "user%3Aemail" in location


# --- Callback: new user ---


@pytest.mark.asyncio
async def test_github_callback_creates_new_user(client: AsyncClient, httpx_mock):
    _mock_token(httpx_mock)
    _mock_profile(httpx_mock, GITHUB_PROFILE_ALICE)

    response = await client.get("/api/v1/auth/github/callback?code=test_code")

    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["token_type"] == "bearer"


@pytest.mark.asyncio
async def test_github_callback_new_user_is_verified(client: AsyncClient, httpx_mock):
    _mock_token(httpx_mock)
    _mock_profile(httpx_mock, GITHUB_PROFILE_ALICE)

    tokens = (await client.get("/api/v1/auth/github/callback?code=code")).json()

    me = await client.get(
        "/api/v1/users/me",
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert me.status_code == 200
    assert me.json()["is_verified"] is True


# --- Callback: returning user with existing OAuth account ---


@pytest.mark.asyncio
async def test_github_callback_returning_user_gets_new_tokens(
    client: AsyncClient, httpx_mock
):
    _mock_token(httpx_mock, "gho_first")
    _mock_profile(httpx_mock, GITHUB_PROFILE_ALICE)
    await client.get("/api/v1/auth/github/callback?code=first_code")

    _mock_token(httpx_mock, "gho_second")
    _mock_profile(httpx_mock, GITHUB_PROFILE_ALICE)
    response = await client.get("/api/v1/auth/github/callback?code=second_code")

    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert "refresh_token" in data


# --- Callback: existing password-registered user, same email ---


@pytest.mark.asyncio
async def test_github_callback_links_account_to_existing_email_user(
    client: AsyncClient, httpx_mock
):
    await client.post("/api/v1/auth/register", json=USER_ALICE)

    _mock_token(httpx_mock)
    _mock_profile(httpx_mock, GITHUB_PROFILE_ALICE)
    response = await client.get("/api/v1/auth/github/callback?code=code123")

    assert response.status_code == 200
    assert "access_token" in response.json()


@pytest.mark.asyncio
async def test_github_callback_marks_existing_user_as_verified(
    client: AsyncClient, httpx_mock
):
    reg = await client.post("/api/v1/auth/register", json=USER_ALICE)
    reg_token = reg.json()["access_token"]

    me_before = await client.get(
        "/api/v1/users/me", headers={"Authorization": f"Bearer {reg_token}"}
    )
    assert me_before.json()["is_verified"] is False

    _mock_token(httpx_mock)
    _mock_profile(httpx_mock, GITHUB_PROFILE_ALICE)
    oauth_tokens = (
        await client.get("/api/v1/auth/github/callback?code=code123")
    ).json()

    me_after = await client.get(
        "/api/v1/users/me",
        headers={"Authorization": f"Bearer {oauth_tokens['access_token']}"},
    )
    assert me_after.json()["is_verified"] is True


# --- Callback: email fetched from /user/emails ---


@pytest.mark.asyncio
async def test_github_callback_fetches_email_from_emails_endpoint(
    client: AsyncClient, httpx_mock
):
    _mock_token(httpx_mock)
    _mock_profile(httpx_mock, {**GITHUB_PROFILE_BOB, "email": ""})
    httpx_mock.add_response(
        url=GITHUB_EMAILS_URL,
        json=[
            {"email": "bob@example.com", "primary": True, "verified": True},
            {"email": "bob-work@example.com", "primary": False, "verified": True},
        ],
    )

    response = await client.get("/api/v1/auth/github/callback?code=code123")

    assert response.status_code == 200
    assert "access_token" in response.json()


# --- Callback: error paths ---


@pytest.mark.asyncio
async def test_github_callback_invalid_code_returns_401(
    client: AsyncClient, httpx_mock
):
    httpx_mock.add_response(
        url=GITHUB_TOKEN_URL,
        method="POST",
        json={"error": "bad_verification_code"},
    )

    response = await client.get("/api/v1/auth/github/callback?code=invalid_code")

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_github_callback_no_email_returns_422(client: AsyncClient, httpx_mock):
    _mock_token(httpx_mock)
    _mock_profile(httpx_mock, {**GITHUB_PROFILE_BOB, "email": None})
    httpx_mock.add_response(url=GITHUB_EMAILS_URL, json=[])

    response = await client.get("/api/v1/auth/github/callback?code=code123")

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_github_callback_no_verified_primary_email_returns_422(
    client: AsyncClient, httpx_mock
):
    _mock_token(httpx_mock)
    _mock_profile(httpx_mock, {**GITHUB_PROFILE_BOB, "email": ""})
    httpx_mock.add_response(
        url=GITHUB_EMAILS_URL,
        json=[
            {"email": "unverified@example.com", "primary": True, "verified": False},
            {"email": "nonprimary@example.com", "primary": False, "verified": True},
        ],
    )

    response = await client.get("/api/v1/auth/github/callback?code=code123")

    assert response.status_code == 422
