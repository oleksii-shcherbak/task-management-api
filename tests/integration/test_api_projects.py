import pytest
from httpx import AsyncClient

USER_ALICE = {
    "email": "alice@example.com",
    "password": "securepassword123",
    "name": "Alice",
}

USER_BOB = {
    "email": "bob@example.com",
    "password": "securepassword123",
    "name": "Bob",
}


# --- Helpers ---


async def register_and_login(client: AsyncClient, user: dict) -> str:
    """Register a user and return their access token."""
    await client.post("/api/v1/auth/register", json=user)
    response = await client.post(
        "/api/v1/auth/login",
        json={"identifier": user["email"], "password": user["password"]},
    )
    return response.json()["access_token"]


async def auth_headers(token: str) -> dict:
    """Return Authorization header dict for a token."""
    return {"Authorization": f"Bearer {token}"}


async def create_project(
    client: AsyncClient, token: str, name: str = "Test Project"
) -> dict:
    """Create a project and return the response data."""
    response = await client.post(
        "/api/v1/projects",
        json={"name": name, "description": "A test project"},
        headers=await auth_headers(token),
    )
    assert response.status_code == 201, f"Project creation failed: {response.text}"
    return response.json()


# --- Project CRUD ---


@pytest.mark.asyncio
async def test_create_project_success(client: AsyncClient):
    token = await register_and_login(client, USER_ALICE)
    response = await client.post(
        "/api/v1/projects",
        json={"name": "My Project", "description": "Test"},
        headers=await auth_headers(token),
    )

    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "My Project"
    assert data["description"] == "Test"
    assert data["status"] == "active"
    assert "id" in data
    assert "owner_id" in data


@pytest.mark.asyncio
async def test_create_project_requires_auth(client: AsyncClient):
    response = await client.post(
        "/api/v1/projects",
        json={"name": "My Project"},
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_list_projects_returns_own_projects(client: AsyncClient):
    token = await register_and_login(client, USER_ALICE)
    await create_project(client, token, "Project A")
    await create_project(client, token, "Project B")

    response = await client.get(
        "/api/v1/projects",
        headers=await auth_headers(token),
    )

    assert response.status_code == 200
    data = response.json()
    assert len(data["items"]) == 2
    names = {p["name"] for p in data["items"]}
    assert names == {"Project A", "Project B"}


@pytest.mark.asyncio
async def test_list_projects_excludes_other_users_projects(client: AsyncClient):
    alice_token = await register_and_login(client, USER_ALICE)
    bob_token = await register_and_login(client, USER_BOB)

    await create_project(client, alice_token, "Alice's Project")
    await create_project(client, bob_token, "Bob's Project")

    response = await client.get(
        "/api/v1/projects",
        headers=await auth_headers(alice_token),
    )

    assert response.status_code == 200
    data = response.json()
    assert len(data["items"]) == 1
    assert data["items"][0]["name"] == "Alice's Project"


@pytest.mark.asyncio
async def test_get_project_success(client: AsyncClient):
    token = await register_and_login(client, USER_ALICE)
    project = await create_project(client, token)

    response = await client.get(
        f"/api/v1/projects/{project['id']}",
        headers=await auth_headers(token),
    )

    assert response.status_code == 200
    assert response.json()["id"] == project["id"]


@pytest.mark.asyncio
async def test_get_project_not_member_returns_403(client: AsyncClient):
    alice_token = await register_and_login(client, USER_ALICE)
    bob_token = await register_and_login(client, USER_BOB)

    project = await create_project(client, alice_token)

    response = await client.get(
        f"/api/v1/projects/{project['id']}",
        headers=await auth_headers(bob_token),
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_update_project_success(client: AsyncClient):
    token = await register_and_login(client, USER_ALICE)
    project = await create_project(client, token)

    response = await client.patch(
        f"/api/v1/projects/{project['id']}",
        json={"name": "Updated Name"},
        headers=await auth_headers(token),
    )

    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Updated Name"
    assert data["description"] == "A test project"  # unchanged


@pytest.mark.asyncio
async def test_update_project_member_cannot_update(client: AsyncClient):
    alice_token = await register_and_login(client, USER_ALICE)
    bob_token = await register_and_login(client, USER_BOB)
    project = await create_project(client, alice_token)

    await client.post(
        f"/api/v1/projects/{project['id']}/members",
        json={"user_id": 2, "role": "member"},
        headers=await auth_headers(alice_token),
    )

    response = await client.patch(
        f"/api/v1/projects/{project['id']}",
        json={"name": "Bob's rename attempt"},
        headers=await auth_headers(bob_token),
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_delete_project_soft_deletes(client: AsyncClient):
    token = await register_and_login(client, USER_ALICE)
    project = await create_project(client, token)

    delete_response = await client.delete(
        f"/api/v1/projects/{project['id']}",
        headers=await auth_headers(token),
    )
    assert delete_response.status_code == 204

    list_response = await client.get(
        "/api/v1/projects",
        headers=await auth_headers(token),
    )
    assert list_response.status_code == 200
    assert list_response.json()["items"] == []


@pytest.mark.asyncio
async def test_delete_project_non_owner_returns_403(client: AsyncClient):
    alice_token = await register_and_login(client, USER_ALICE)
    bob_token = await register_and_login(client, USER_BOB)
    project = await create_project(client, alice_token)

    response = await client.delete(
        f"/api/v1/projects/{project['id']}",
        headers=await auth_headers(bob_token),
    )
    assert response.status_code == 403


# --- Member Management ---


@pytest.mark.asyncio
async def test_add_member_success(client: AsyncClient):
    alice_token = await register_and_login(client, USER_ALICE)
    await register_and_login(client, USER_BOB)
    project = await create_project(client, alice_token)

    # Bob is user_id=2 since he was the second user registered in this test
    response = await client.post(
        f"/api/v1/projects/{project['id']}/members",
        json={"user_id": 2, "role": "member"},
        headers=await auth_headers(alice_token),
    )
    assert response.status_code == 201
    data = response.json()
    assert data["role"] == "member"


@pytest.mark.asyncio
async def test_add_member_duplicate_returns_409(client: AsyncClient):
    alice_token = await register_and_login(client, USER_ALICE)
    await register_and_login(client, USER_BOB)
    project = await create_project(client, alice_token)

    await client.post(
        f"/api/v1/projects/{project['id']}/members",
        json={"user_id": 2, "role": "member"},
        headers=await auth_headers(alice_token),
    )
    response = await client.post(
        f"/api/v1/projects/{project['id']}/members",
        json={"user_id": 2, "role": "member"},
        headers=await auth_headers(alice_token),
    )
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_list_members_includes_owner(client: AsyncClient):
    token = await register_and_login(client, USER_ALICE)
    project = await create_project(client, token)

    response = await client.get(
        f"/api/v1/projects/{project['id']}/members",
        headers=await auth_headers(token),
    )

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["role"] == "owner"


@pytest.mark.asyncio
async def test_remove_member_success(client: AsyncClient):
    alice_token = await register_and_login(client, USER_ALICE)
    await register_and_login(client, USER_BOB)
    project = await create_project(client, alice_token)

    await client.post(
        f"/api/v1/projects/{project['id']}/members",
        json={"user_id": 2, "role": "member"},
        headers=await auth_headers(alice_token),
    )

    response = await client.delete(
        f"/api/v1/projects/{project['id']}/members/2",
        headers=await auth_headers(alice_token),
    )
    assert response.status_code == 204


@pytest.mark.asyncio
async def test_cannot_remove_owner(client: AsyncClient):
    token = await register_and_login(client, USER_ALICE)
    project = await create_project(client, token)

    response = await client.delete(
        f"/api/v1/projects/{project['id']}/members/1",
        headers=await auth_headers(token),
    )
    assert response.status_code == 403


# --- Member Search ---


@pytest.mark.asyncio
async def test_member_search_returns_matching_members(client: AsyncClient):
    token = await register_and_login(client, USER_ALICE)
    token_bob = await register_and_login(client, {**USER_BOB, "username": "bob_worker"})
    project = await create_project(client, token)
    headers = await auth_headers(token)

    bob_me = (
        await client.get("/api/v1/users/me", headers=await auth_headers(token_bob))
    ).json()
    await client.post(
        f"/api/v1/projects/{project['id']}/members",
        headers=headers,
        json={"user_id": bob_me["id"]},
    )

    response = await client.get(
        f"/api/v1/projects/{project['id']}/members/search",
        headers=headers,
        params={"q": "bob"},
    )
    assert response.status_code == 200
    results = response.json()
    assert len(results) == 1
    assert results[0]["username"] == "bob_worker"
    assert "full_name" in results[0]
    assert "avatar_url" in results[0]


@pytest.mark.asyncio
async def test_member_search_does_not_leak_non_members(client: AsyncClient):
    token = await register_and_login(client, USER_ALICE)
    await register_and_login(client, {**USER_BOB, "username": "bob_outside"})
    project = await create_project(client, token)
    headers = await auth_headers(token)

    response = await client.get(
        f"/api/v1/projects/{project['id']}/members/search",
        headers=headers,
        params={"q": "bob"},
    )
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_member_search_non_member_returns_403(client: AsyncClient):
    token = await register_and_login(client, USER_ALICE)
    token_bob = await register_and_login(client, USER_BOB)
    project = await create_project(client, token)

    response = await client.get(
        f"/api/v1/projects/{project['id']}/members/search",
        headers=await auth_headers(token_bob),
        params={"q": "a"},
    )
    assert response.status_code == 403


# --- Pagination ---


@pytest.mark.asyncio
async def test_list_projects_pagination(client: AsyncClient):
    token = await register_and_login(client, USER_ALICE)
    for i in range(3):
        await create_project(client, token, f"Project {i}")

    response = await client.get(
        "/api/v1/projects",
        params={"limit": 2},
        headers=await auth_headers(token),
    )
    assert response.status_code == 200
    page1 = response.json()
    assert len(page1["items"]) == 2
    assert page1["has_more"] is True
    assert page1["next_cursor"] is not None

    response2 = await client.get(
        "/api/v1/projects",
        params={"limit": 2, "cursor": page1["next_cursor"]},
        headers=await auth_headers(token),
    )
    assert response2.status_code == 200
    page2 = response2.json()
    assert len(page2["items"]) == 1
    assert page2["has_more"] is False
    assert page2["next_cursor"] is None

    ids1 = {p["id"] for p in page1["items"]}
    ids2 = {p["id"] for p in page2["items"]}
    assert ids1.isdisjoint(ids2)
    assert len(ids1 | ids2) == 3


@pytest.mark.asyncio
async def test_list_projects_no_more_pages(client: AsyncClient):
    token = await register_and_login(client, USER_ALICE)
    await create_project(client, token, "Only Project")

    response = await client.get(
        "/api/v1/projects",
        params={"limit": 20},
        headers=await auth_headers(token),
    )
    data = response.json()
    assert data["has_more"] is False
    assert data["next_cursor"] is None


@pytest.mark.asyncio
async def test_list_projects_invalid_cursor_returns_422(client: AsyncClient):
    token = await register_and_login(client, USER_ALICE)

    response = await client.get(
        "/api/v1/projects",
        params={"cursor": "not-valid-base64!!!"},
        headers=await auth_headers(token),
    )
    assert response.status_code == 422


# --- Caching ---


@pytest.mark.asyncio
async def test_remove_member_invalidates_membership_cache(client: AsyncClient):
    alice_token = await register_and_login(client, USER_ALICE)
    bob_token = await register_and_login(client, USER_BOB)
    bob_id = (
        await client.get("/api/v1/users/me", headers=await auth_headers(bob_token))
    ).json()["id"]

    project = await create_project(client, alice_token)
    project_id = project["id"]

    await client.post(
        f"/api/v1/projects/{project_id}/members",
        json={"user_id": bob_id, "role": "member"},
        headers=await auth_headers(alice_token),
    )

    # Bob accesses the project - his membership is now cached
    response = await client.get(
        f"/api/v1/projects/{project_id}",
        headers=await auth_headers(bob_token),
    )
    assert response.status_code == 200

    # Alice removes Bob
    await client.delete(
        f"/api/v1/projects/{project_id}/members/{bob_id}",
        headers=await auth_headers(alice_token),
    )

    # Bob's next request must be rejected - stale cache would incorrectly allow it
    response = await client.get(
        f"/api/v1/projects/{project_id}",
        headers=await auth_headers(bob_token),
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_list_project_statuses_cache_returns_correct_data(client: AsyncClient):
    token = await register_and_login(client, USER_ALICE)
    project = await create_project(client, token)
    project_id = project["id"]
    headers = await auth_headers(token)

    first = await client.get(f"/api/v1/projects/{project_id}/statuses", headers=headers)
    assert first.status_code == 200

    # Second call hits the cache - data must be identical
    second = await client.get(
        f"/api/v1/projects/{project_id}/statuses", headers=headers
    )
    assert second.status_code == 200
    assert second.json() == first.json()


# --- Notification enqueueing ---


@pytest.mark.asyncio
async def test_add_member_enqueues_invitation_notification(
    client: AsyncClient, arq_mock
):
    alice_token = await register_and_login(client, USER_ALICE)
    await register_and_login(client, USER_BOB)
    project = await create_project(client, alice_token)
    arq_mock.enqueue_job.reset_mock()

    await client.post(
        f"/api/v1/projects/{project['id']}/members",
        json={"user_id": 2, "role": "member"},
        headers=await auth_headers(alice_token),
    )

    arq_mock.enqueue_job.assert_called_once_with(
        "send_project_invitation",
        user_id=2,
        project_name=project["name"],
        role="member",
    )
