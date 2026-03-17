import pytest
from httpx import AsyncClient

USER = {
    "email": "alice@example.com",
    "password": "securepassword123",
    "name": "Alice",
}

OTHER_USER = {
    "email": "bob@example.com",
    "password": "securepassword456",
    "name": "Bob",
}


async def register_and_login(client: AsyncClient, user: dict | None = None) -> str:
    if user is None:
        user = USER
    await client.post("/api/v1/auth/register", json=user)
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": user["email"], "password": user["password"]},
    )
    return response.json()["access_token"]


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# --- GET /users/me ---


@pytest.mark.asyncio
async def test_get_me_returns_profile(client: AsyncClient):
    token = await register_and_login(client)
    response = await client.get("/api/v1/users/me", headers=auth(token))

    assert response.status_code == 200
    data = response.json()
    assert data["email"] == USER["email"]
    assert data["name"] == USER["name"]
    assert "password_hash" not in data
    assert "id" in data
    assert "created_at" in data
    assert "is_verified" in data
    assert "avatar_url" in data


@pytest.mark.asyncio
async def test_get_me_unauthenticated_returns_401(client: AsyncClient):
    response = await client.get("/api/v1/users/me")
    assert response.status_code == 401


# --- PATCH /users/me ---


@pytest.mark.asyncio
async def test_update_me_name(client: AsyncClient):
    token = await register_and_login(client)
    response = await client.patch(
        "/api/v1/users/me", headers=auth(token), json={"name": "Alicia"}
    )

    assert response.status_code == 200
    assert response.json()["name"] == "Alicia"


@pytest.mark.asyncio
async def test_update_me_email(client: AsyncClient):
    token = await register_and_login(client)
    response = await client.patch(
        "/api/v1/users/me", headers=auth(token), json={"email": "alice2@example.com"}
    )

    assert response.status_code == 200
    data = response.json()
    assert data["email"] == "alice2@example.com"
    assert data["is_verified"] is False


@pytest.mark.asyncio
async def test_update_me_duplicate_email_returns_409(client: AsyncClient):
    token_alice = await register_and_login(client, USER)
    await register_and_login(client, OTHER_USER)

    response = await client.patch(
        "/api/v1/users/me",
        headers=auth(token_alice),
        json={"email": OTHER_USER["email"]},
    )

    assert response.status_code == 409


@pytest.mark.asyncio
async def test_update_me_same_email_does_not_reset_verified(client: AsyncClient):
    token = await register_and_login(client)
    response = await client.patch(
        "/api/v1/users/me", headers=auth(token), json={"email": USER["email"]}
    )

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_update_me_empty_name_returns_422(client: AsyncClient):
    token = await register_and_login(client)
    response = await client.patch(
        "/api/v1/users/me", headers=auth(token), json={"name": ""}
    )
    assert response.status_code == 422


# --- PATCH /users/me/password ---


@pytest.mark.asyncio
async def test_change_password_success(client: AsyncClient):
    token = await register_and_login(client)
    response = await client.patch(
        "/api/v1/users/me/password",
        headers=auth(token),
        json={"current_password": USER["password"], "new_password": "newpassword456"},
    )
    assert response.status_code == 204


@pytest.mark.asyncio
async def test_change_password_wrong_current_returns_401(client: AsyncClient):
    token = await register_and_login(client)
    response = await client.patch(
        "/api/v1/users/me/password",
        headers=auth(token),
        json={"current_password": "wrongpassword", "new_password": "newpassword456"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_change_password_revokes_refresh_tokens(client: AsyncClient):
    await client.post("/api/v1/auth/register", json=USER)
    login_resp = await client.post(
        "/api/v1/auth/login",
        json={"email": USER["email"], "password": USER["password"]},
    )
    tokens = login_resp.json()
    access_token = tokens["access_token"]
    refresh_token = tokens["refresh_token"]

    await client.patch(
        "/api/v1/users/me/password",
        headers=auth(access_token),
        json={"current_password": USER["password"], "new_password": "newpassword456"},
    )

    refresh_resp = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": refresh_token}
    )
    assert refresh_resp.status_code == 401


@pytest.mark.asyncio
async def test_change_password_short_new_password_returns_422(client: AsyncClient):
    token = await register_and_login(client)
    response = await client.patch(
        "/api/v1/users/me/password",
        headers=auth(token),
        json={"current_password": USER["password"], "new_password": "short"},
    )
    assert response.status_code == 422


# --- DELETE /users/me ---


@pytest.mark.asyncio
async def test_delete_me_returns_204(client: AsyncClient):
    token = await register_and_login(client)
    response = await client.delete("/api/v1/users/me", headers=auth(token))
    assert response.status_code == 204


@pytest.mark.asyncio
async def test_deleted_user_cannot_authenticate(client: AsyncClient):
    token = await register_and_login(client)
    await client.delete("/api/v1/users/me", headers=auth(token))

    response = await client.post(
        "/api/v1/auth/login",
        json={"email": USER["email"], "password": USER["password"]},
    )
    assert response.status_code == 401


# --- GET /users/{id} ---


@pytest.mark.asyncio
async def test_get_public_profile(client: AsyncClient):
    token = await register_and_login(client)
    me = (await client.get("/api/v1/users/me", headers=auth(token))).json()

    response = await client.get(f"/api/v1/users/{me['id']}")
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == USER["name"]
    assert "email" not in data
    assert "password_hash" not in data


@pytest.mark.asyncio
async def test_get_public_profile_nonexistent_returns_404(client: AsyncClient):
    response = await client.get("/api/v1/users/99999")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_get_public_profile_deleted_user_returns_404(client: AsyncClient):
    token = await register_and_login(client)
    me = (await client.get("/api/v1/users/me", headers=auth(token))).json()
    await client.delete("/api/v1/users/me", headers=auth(token))

    response = await client.get(f"/api/v1/users/{me['id']}")
    assert response.status_code == 404
