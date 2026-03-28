import pytest
from httpx import AsyncClient

USER_ALICE = {
    "email": "alice_s@example.com",
    "password": "securepassword123",
    "name": "Alice",
}
USER_BOB = {
    "email": "bob_s@example.com",
    "password": "securepassword123",
    "name": "Bob",
}


# --- Helpers ---


async def register_and_login(client: AsyncClient, user: dict) -> str:
    await client.post("/api/v1/auth/register", json=user)
    response = await client.post(
        "/api/v1/auth/login",
        json={"identifier": user["email"], "password": user["password"]},
    )
    return response.json()["access_token"]


async def auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def create_project(client: AsyncClient, token: str) -> dict:
    response = await client.post(
        "/api/v1/projects",
        json={"name": "Status Test Project"},
        headers=await auth_headers(token),
    )
    assert response.status_code == 201
    return response.json()


async def get_statuses(client: AsyncClient, token: str, project_id: int) -> list[dict]:
    response = await client.get(
        f"/api/v1/projects/{project_id}/statuses",
        headers=await auth_headers(token),
    )
    assert response.status_code == 200
    return response.json()


async def create_task(
    client: AsyncClient, token: str, project_id: int, status_id: int
) -> dict:
    response = await client.post(
        f"/api/v1/projects/{project_id}/tasks",
        json={"title": "Test Task", "status_id": status_id},
        headers=await auth_headers(token),
    )
    assert response.status_code == 201
    return response.json()


# --- POST /projects/{id}/statuses ---


@pytest.mark.asyncio
async def test_create_status_success(client: AsyncClient):
    token = await register_and_login(client, USER_ALICE)
    project = await create_project(client, token)

    response = await client.post(
        f"/api/v1/projects/{project['id']}/statuses",
        json={"name": "Review", "color": "#a855f7", "type": "started"},
        headers=await auth_headers(token),
    )

    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "Review"
    assert data["color"] == "#a855f7"
    assert data["type"] == "started"
    assert data["is_default"] is False


@pytest.mark.asyncio
async def test_create_status_position_auto_assigned(client: AsyncClient):
    token = await register_and_login(client, USER_ALICE)
    project = await create_project(client, token)

    response = await client.post(
        f"/api/v1/projects/{project['id']}/statuses",
        json={"name": "Cancelled", "color": "#ef4444", "type": "cancelled"},
        headers=await auth_headers(token),
    )

    assert response.status_code == 201
    # 3 defaults already exist at positions 1-3; new one gets position 4
    assert response.json()["position"] == 4


@pytest.mark.asyncio
async def test_create_status_member_forbidden(client: AsyncClient):
    alice_token = await register_and_login(client, USER_ALICE)
    bob_token = await register_and_login(client, USER_BOB)
    project = await create_project(client, alice_token)

    # Bob is not a member of Alice's project → 403
    response = await client.post(
        f"/api/v1/projects/{project['id']}/statuses",
        json={"name": "Review", "color": "#a855f7", "type": "started"},
        headers=await auth_headers(bob_token),
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_create_status_duplicate_name_rejected(client: AsyncClient):
    token = await register_and_login(client, USER_ALICE)
    project = await create_project(client, token)

    response = await client.post(
        f"/api/v1/projects/{project['id']}/statuses",
        json={"name": "backlog", "color": "#a855f7", "type": "unstarted"},
        headers=await auth_headers(token),
    )

    assert response.status_code == 409


@pytest.mark.asyncio
async def test_create_status_invalid_color_rejected(client: AsyncClient):
    token = await register_and_login(client, USER_ALICE)
    project = await create_project(client, token)

    response = await client.post(
        f"/api/v1/projects/{project['id']}/statuses",
        json={"name": "Review", "color": "purple", "type": "started"},
        headers=await auth_headers(token),
    )

    assert response.status_code == 422


# --- PATCH /projects/{id}/statuses/{status_id} ---


@pytest.mark.asyncio
async def test_update_status_rename_success(client: AsyncClient):
    token = await register_and_login(client, USER_ALICE)
    project = await create_project(client, token)
    statuses = await get_statuses(client, token, project["id"])
    backlog = next(s for s in statuses if s["name"] == "Backlog")

    response = await client.patch(
        f"/api/v1/projects/{project['id']}/statuses/{backlog['id']}",
        json={"name": "To Do"},
        headers=await auth_headers(token),
    )

    assert response.status_code == 200
    assert response.json()["name"] == "To Do"


@pytest.mark.asyncio
async def test_update_status_set_default_swaps(client: AsyncClient):
    token = await register_and_login(client, USER_ALICE)
    project = await create_project(client, token)
    statuses = await get_statuses(client, token, project["id"])
    in_progress = next(s for s in statuses if s["name"] == "In Progress")

    response = await client.patch(
        f"/api/v1/projects/{project['id']}/statuses/{in_progress['id']}",
        json={"is_default": True},
        headers=await auth_headers(token),
    )

    assert response.status_code == 200
    assert response.json()["is_default"] is True

    updated = await get_statuses(client, token, project["id"])
    defaults = [s for s in updated if s["is_default"]]
    assert len(defaults) == 1
    assert defaults[0]["name"] == "In Progress"


@pytest.mark.asyncio
async def test_update_status_unset_default_rejected(client: AsyncClient):
    token = await register_and_login(client, USER_ALICE)
    project = await create_project(client, token)
    statuses = await get_statuses(client, token, project["id"])
    backlog = next(s for s in statuses if s["name"] == "Backlog")

    response = await client.patch(
        f"/api/v1/projects/{project['id']}/statuses/{backlog['id']}",
        json={"is_default": False},
        headers=await auth_headers(token),
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_update_status_reorder(client: AsyncClient):
    token = await register_and_login(client, USER_ALICE)
    project = await create_project(client, token)
    statuses = await get_statuses(client, token, project["id"])
    done = next(s for s in statuses if s["name"] == "Done")  # position 3

    await client.patch(
        f"/api/v1/projects/{project['id']}/statuses/{done['id']}",
        json={"position": 1},
        headers=await auth_headers(token),
    )

    updated = await get_statuses(client, token, project["id"])
    by_position = {s["position"]: s["name"] for s in updated}
    assert by_position[1] == "Done"
    assert by_position[2] == "Backlog"
    assert by_position[3] == "In Progress"


@pytest.mark.asyncio
async def test_update_status_not_found(client: AsyncClient):
    token = await register_and_login(client, USER_ALICE)
    project = await create_project(client, token)

    response = await client.patch(
        f"/api/v1/projects/{project['id']}/statuses/99999",
        json={"name": "Ghost"},
        headers=await auth_headers(token),
    )

    assert response.status_code == 404


# --- DELETE /projects/{id}/statuses/{status_id} ---


@pytest.mark.asyncio
async def test_delete_status_success(client: AsyncClient):
    token = await register_and_login(client, USER_ALICE)
    project = await create_project(client, token)
    statuses = await get_statuses(client, token, project["id"])
    in_progress = next(s for s in statuses if s["name"] == "In Progress")  # position 2

    response = await client.delete(
        f"/api/v1/projects/{project['id']}/statuses/{in_progress['id']}",
        headers=await auth_headers(token),
    )

    assert response.status_code == 204

    updated = await get_statuses(client, token, project["id"])
    assert len(updated) == 2
    # Gap closed: Done moved from position 3 to position 2
    done = next(s for s in updated if s["name"] == "Done")
    assert done["position"] == 2


@pytest.mark.asyncio
async def test_delete_status_migrates_tasks(client: AsyncClient):
    token = await register_and_login(client, USER_ALICE)
    project = await create_project(client, token)
    statuses = await get_statuses(client, token, project["id"])
    in_progress = next(s for s in statuses if s["name"] == "In Progress")
    done = next(s for s in statuses if s["name"] == "Done")

    task = await create_task(client, token, project["id"], in_progress["id"])

    response = await client.delete(
        f"/api/v1/projects/{project['id']}/statuses/{in_progress['id']}",
        params={"move_tasks_to": done["id"]},
        headers=await auth_headers(token),
    )

    assert response.status_code == 204

    task_response = await client.get(
        f"/api/v1/tasks/{task['id']}",
        headers=await auth_headers(token),
    )
    assert task_response.json()["status"]["id"] == done["id"]


@pytest.mark.asyncio
async def test_delete_status_with_tasks_no_move_rejected(client: AsyncClient):
    token = await register_and_login(client, USER_ALICE)
    project = await create_project(client, token)
    statuses = await get_statuses(client, token, project["id"])
    in_progress = next(s for s in statuses if s["name"] == "In Progress")

    await create_task(client, token, project["id"], in_progress["id"])

    response = await client.delete(
        f"/api/v1/projects/{project['id']}/statuses/{in_progress['id']}",
        headers=await auth_headers(token),
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_delete_default_status_rejected(client: AsyncClient):
    token = await register_and_login(client, USER_ALICE)
    project = await create_project(client, token)
    statuses = await get_statuses(client, token, project["id"])
    backlog = next(s for s in statuses if s["is_default"])

    response = await client.delete(
        f"/api/v1/projects/{project['id']}/statuses/{backlog['id']}",
        headers=await auth_headers(token),
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_delete_move_to_same_status_rejected(client: AsyncClient):
    token = await register_and_login(client, USER_ALICE)
    project = await create_project(client, token)
    statuses = await get_statuses(client, token, project["id"])
    in_progress = next(s for s in statuses if s["name"] == "In Progress")

    response = await client.delete(
        f"/api/v1/projects/{project['id']}/statuses/{in_progress['id']}",
        params={"move_tasks_to": in_progress["id"]},
        headers=await auth_headers(token),
    )

    assert response.status_code == 422


# --- Cache invalidation ---


@pytest.mark.asyncio
async def test_create_status_invalidates_cache(client: AsyncClient):
    token = await register_and_login(client, USER_ALICE)
    project = await create_project(client, token)

    # Prime the cache with the initial 3 statuses
    before = await get_statuses(client, token, project["id"])
    assert len(before) == 3

    await client.post(
        f"/api/v1/projects/{project['id']}/statuses",
        json={"name": "Shipped", "color": "#10b981", "type": "completed"},
        headers=await auth_headers(token),
    )

    after = await get_statuses(client, token, project["id"])
    assert len(after) == 4
    assert any(s["name"] == "Shipped" for s in after)
