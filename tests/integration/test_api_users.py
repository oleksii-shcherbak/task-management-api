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
        json={"identifier": user["email"], "password": user["password"]},
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


@pytest.mark.asyncio
async def test_update_username_success(client: AsyncClient):
    token = await register_and_login(client)
    response = await client.patch(
        "/api/v1/users/me", headers=auth(token), json={"username": "new_handle"}
    )
    assert response.status_code == 200
    assert response.json()["username"] == "new_handle"


@pytest.mark.asyncio
async def test_update_username_duplicate_returns_409(client: AsyncClient):
    token_alice = await register_and_login(client, USER)
    await register_and_login(
        client,
        {**OTHER_USER, "username": "bobhandle"}
        if "username" not in OTHER_USER
        else OTHER_USER,
    )

    response = await client.patch(
        "/api/v1/users/me",
        headers=auth(token_alice),
        json={"username": "bobhandle"},
    )
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_update_username_reserved_returns_422(client: AsyncClient):
    token = await register_and_login(client)
    response = await client.patch(
        "/api/v1/users/me", headers=auth(token), json={"username": "admin"}
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_get_me_includes_username(client: AsyncClient):
    token = await register_and_login(client)
    response = await client.get("/api/v1/users/me", headers=auth(token))
    assert "username" in response.json()


@pytest.mark.asyncio
async def test_update_username_cooldown_blocks_second_change(client: AsyncClient):
    token = await register_and_login(client)
    await client.patch(
        "/api/v1/users/me", headers=auth(token), json={"username": "first_name"}
    )
    response = await client.patch(
        "/api/v1/users/me", headers=auth(token), json={"username": "second_name"}
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_update_username_recently_released_is_reserved(client: AsyncClient):
    # Alice claims a specific username
    token_alice = await register_and_login(client, USER)
    await client.patch(
        "/api/v1/users/me",
        headers=auth(token_alice),
        json={"username": "prized_handle"},
    )

    # Bob tries to claim the same active username - blocked via active user check
    token_bob = await register_and_login(client, OTHER_USER)
    response = await client.patch(
        "/api/v1/users/me", headers=auth(token_bob), json={"username": "prized_handle"}
    )
    assert response.status_code == 409


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
        json={"identifier": USER["email"], "password": USER["password"]},
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
        json={"identifier": USER["email"], "password": USER["password"]},
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
async def test_deleted_account_email_can_be_reregistered(client: AsyncClient):
    token = await register_and_login(client)
    await client.delete("/api/v1/users/me", headers=auth(token))

    response = await client.post("/api/v1/auth/register", json=USER)
    assert response.status_code == 201


@pytest.mark.asyncio
async def test_get_public_profile_deleted_user_returns_404(client: AsyncClient):
    token = await register_and_login(client)
    me = (await client.get("/api/v1/users/me", headers=auth(token))).json()
    await client.delete("/api/v1/users/me", headers=auth(token))

    response = await client.get(f"/api/v1/users/{me['id']}")
    assert response.status_code == 404


# --- POST /users/me/avatar ---


JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 32
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
EXE_BYTES = b"MZ" + b"\x00" * 32


@pytest.mark.asyncio
async def test_upload_avatar_success(client: AsyncClient):
    token = await register_and_login(client)

    r = await client.post(
        "/api/v1/users/me/avatar",
        files={"file": ("avatar.jpg", JPEG_BYTES, "application/octet-stream")},
        headers=auth(token),
    )

    assert r.status_code == 200
    data = r.json()
    assert data["avatar_url"] is not None


@pytest.mark.asyncio
async def test_upload_avatar_replaces_previous(client: AsyncClient):
    token = await register_and_login(client)

    await client.post(
        "/api/v1/users/me/avatar",
        files={"file": ("first.jpg", JPEG_BYTES, "application/octet-stream")},
        headers=auth(token),
    )
    first_url = (await client.get("/api/v1/users/me", headers=auth(token))).json()[
        "avatar_url"
    ]

    await client.post(
        "/api/v1/users/me/avatar",
        files={"file": ("second.png", PNG_BYTES, "application/octet-stream")},
        headers=auth(token),
    )
    second_url = (await client.get("/api/v1/users/me", headers=auth(token))).json()[
        "avatar_url"
    ]

    assert second_url != first_url


@pytest.mark.asyncio
async def test_upload_avatar_disallowed_type(client: AsyncClient):
    token = await register_and_login(client)

    r = await client.post(
        "/api/v1/users/me/avatar",
        files={"file": ("bad.exe", EXE_BYTES, "application/octet-stream")},
        headers=auth(token),
    )

    assert r.status_code == 422


@pytest.mark.asyncio
async def test_upload_avatar_oversized(client: AsyncClient):
    token = await register_and_login(client)
    oversized = JPEG_BYTES + b"\x00" * (2 * 1024 * 1024)

    r = await client.post(
        "/api/v1/users/me/avatar",
        files={"file": ("big.jpg", oversized, "application/octet-stream")},
        headers=auth(token),
    )

    assert r.status_code == 422


# --- DELETE /users/me/avatar ---


@pytest.mark.asyncio
async def test_delete_avatar_clears_url(client: AsyncClient):
    token = await register_and_login(client)

    await client.post(
        "/api/v1/users/me/avatar",
        files={"file": ("avatar.jpg", JPEG_BYTES, "application/octet-stream")},
        headers=auth(token),
    )

    r = await client.delete("/api/v1/users/me/avatar", headers=auth(token))
    assert r.status_code == 204

    me = (await client.get("/api/v1/users/me", headers=auth(token))).json()
    assert me["avatar_url"] is None


@pytest.mark.asyncio
async def test_delete_avatar_when_none_is_noop(client: AsyncClient):
    token = await register_and_login(client)

    r = await client.delete("/api/v1/users/me/avatar", headers=auth(token))
    assert r.status_code == 204


# --- GET /users/me/mentions ---


async def _setup_mention_project(client, actor_token, mentioned_token):
    """Return (project, task) after adding mentioned user as a project member."""
    actor_id = (await client.get("/api/v1/users/me", headers=auth(actor_token))).json()[
        "id"
    ]
    mentioned_id = (
        await client.get("/api/v1/users/me", headers=auth(mentioned_token))
    ).json()["id"]

    project = (
        await client.post(
            "/api/v1/projects",
            json={"name": "Inbox Project"},
            headers=auth(actor_token),
        )
    ).json()
    task = (
        await client.post(
            f"/api/v1/projects/{project['id']}/tasks",
            json={"title": "Inbox Task"},
            headers=auth(actor_token),
        )
    ).json()
    await client.post(
        f"/api/v1/projects/{project['id']}/members",
        json={"user_id": mentioned_id},
        headers=auth(actor_token),
    )
    return project, task, actor_id, mentioned_id


@pytest.mark.asyncio
async def test_mentions_inbox_includes_comment_mention(client: AsyncClient):
    alice_token = await register_and_login(client, {**USER, "username": "alice_inbox"})
    bob_token = await register_and_login(
        client, {**OTHER_USER, "username": "bob_inbox"}
    )
    project, task, _, _ = await _setup_mention_project(client, alice_token, bob_token)

    await client.post(
        f"/api/v1/projects/{project['id']}/tasks/{task['id']}/comments",
        json={"content": "Hey @bob_inbox, take a look!"},
        headers=auth(alice_token),
    )

    r = await client.get("/api/v1/users/me/mentions", headers=auth(bob_token))
    assert r.status_code == 200
    data = r.json()
    assert data["has_more"] is False
    assert len(data["items"]) == 1
    item = data["items"][0]
    assert item["source_type"] == "comment"
    assert item["project_id"] == project["id"]
    assert item["actor_name"] == USER["name"]
    assert item["actor_username"] == "alice_inbox"
    assert "bob_inbox" in item["body_excerpt"]


@pytest.mark.asyncio
async def test_mentions_inbox_includes_task_mention(client: AsyncClient):
    alice_token = await register_and_login(client, {**USER, "username": "alice_inbox"})
    bob_token = await register_and_login(
        client, {**OTHER_USER, "username": "bob_inbox"}
    )
    project, _, _, _ = await _setup_mention_project(client, alice_token, bob_token)

    await client.post(
        f"/api/v1/projects/{project['id']}/tasks",
        json={"title": "Task for Bob", "description": "Hey @bob_inbox!"},
        headers=auth(alice_token),
    )

    r = await client.get("/api/v1/users/me/mentions", headers=auth(bob_token))
    assert r.status_code == 200
    items = r.json()["items"]
    assert any(i["source_type"] == "task" for i in items)
    task_item = next(i for i in items if i["source_type"] == "task")
    assert task_item["actor_username"] == "alice_inbox"
    assert "bob_inbox" in task_item["body_excerpt"]


@pytest.mark.asyncio
async def test_mentions_inbox_ordered_newest_first(client: AsyncClient):
    alice_token = await register_and_login(client, {**USER, "username": "alice_inbox"})
    bob_token = await register_and_login(
        client, {**OTHER_USER, "username": "bob_inbox"}
    )
    project, task, _, _ = await _setup_mention_project(client, alice_token, bob_token)

    await client.post(
        f"/api/v1/projects/{project['id']}/tasks/{task['id']}/comments",
        json={"content": "First mention @bob_inbox"},
        headers=auth(alice_token),
    )
    await client.post(
        f"/api/v1/projects/{project['id']}/tasks/{task['id']}/comments",
        json={"content": "Second mention @bob_inbox"},
        headers=auth(alice_token),
    )

    r = await client.get("/api/v1/users/me/mentions", headers=auth(bob_token))
    items = r.json()["items"]
    assert len(items) == 2
    assert "Second" in items[0]["body_excerpt"]
    assert "First" in items[1]["body_excerpt"]


@pytest.mark.asyncio
async def test_mentions_inbox_unauthenticated_returns_401(client: AsyncClient):
    r = await client.get("/api/v1/users/me/mentions")
    assert r.status_code == 401
