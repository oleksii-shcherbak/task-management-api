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
        json={"email": user["email"], "password": user["password"]},
    )
    return response.json()["access_token"]


def auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def create_project(client: AsyncClient, token: str) -> dict:
    response = await client.post(
        "/api/v1/projects",
        json={"name": "Test Project", "description": "A test project"},
        headers=auth_headers(token),
    )
    assert response.status_code == 201, f"Project creation failed: {response.text}"
    return response.json()


async def get_statuses(client: AsyncClient, token: str, project_id: int) -> dict:
    """Returns a dict of status name -> status object."""
    response = await client.get(
        f"/api/v1/projects/{project_id}/statuses",
        headers=auth_headers(token),
    )
    assert response.status_code == 200
    return {s["name"]: s for s in response.json()}


async def create_task(
    client: AsyncClient, token: str, project_id: int, **kwargs
) -> dict:
    body = {"title": "Test Task", **kwargs}
    response = await client.post(
        f"/api/v1/projects/{project_id}/tasks",
        json=body,
        headers=auth_headers(token),
    )
    assert response.status_code == 201, f"Task creation failed: {response.text}"
    return response.json()


async def add_member(
    client: AsyncClient,
    token: str,
    project_id: int,
    user_id: int,
    role: str = "member",
) -> None:
    response = await client.post(
        f"/api/v1/projects/{project_id}/members",
        json={"user_id": user_id, "role": role},
        headers=auth_headers(token),
    )
    assert response.status_code == 201, f"Add member failed: {response.text}"


# --- Create Task ---


@pytest.mark.asyncio
async def test_create_task_defaults_to_default_status(client: AsyncClient):
    token = await register_and_login(client, USER_ALICE)
    project = await create_project(client, token)
    statuses = await get_statuses(client, token, project["id"])

    task = await create_task(client, token, project["id"], title="My Task")

    assert task["status"]["id"] == statuses["Backlog"]["id"]
    assert task["status"]["name"] == "Backlog"
    assert task["position"] == 1


@pytest.mark.asyncio
async def test_create_task_with_explicit_status(client: AsyncClient):
    token = await register_and_login(client, USER_ALICE)
    project = await create_project(client, token)
    statuses = await get_statuses(client, token, project["id"])

    task = await create_task(
        client,
        token,
        project["id"],
        title="My Task",
        status_id=statuses["In Progress"]["id"],
    )

    assert task["status"]["name"] == "In Progress"
    assert task["position"] == 1


@pytest.mark.asyncio
async def test_create_task_position_increments(client: AsyncClient):
    token = await register_and_login(client, USER_ALICE)
    project = await create_project(client, token)

    t1 = await create_task(client, token, project["id"], title="Task A")
    t2 = await create_task(client, token, project["id"], title="Task B")
    t3 = await create_task(client, token, project["id"], title="Task C")

    assert t1["position"] == 1
    assert t2["position"] == 2
    assert t3["position"] == 3


@pytest.mark.asyncio
async def test_create_task_non_member_forbidden(client: AsyncClient):
    alice_token = await register_and_login(client, USER_ALICE)
    bob_token = await register_and_login(client, USER_BOB)
    project = await create_project(client, alice_token)

    response = await client.post(
        f"/api/v1/projects/{project['id']}/tasks",
        json={"title": "Sneaky Task"},
        headers=auth_headers(bob_token),
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_create_task_regular_member_forbidden(client: AsyncClient):
    alice_token = await register_and_login(client, USER_ALICE)
    bob_token = await register_and_login(client, USER_BOB)
    project = await create_project(client, alice_token)
    await add_member(client, alice_token, project["id"], user_id=2, role="member")

    response = await client.post(
        f"/api/v1/projects/{project['id']}/tasks",
        json={"title": "Task by Member"},
        headers=auth_headers(bob_token),
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_create_task_assignee_not_member_forbidden(client: AsyncClient):
    alice_token = await register_and_login(client, USER_ALICE)
    await register_and_login(client, USER_BOB)  # Bob exists but is not in project
    project = await create_project(client, alice_token)

    response = await client.post(
        f"/api/v1/projects/{project['id']}/tasks",
        json={"title": "Task", "assignee_id": 2},
        headers=auth_headers(alice_token),
    )
    assert response.status_code == 403
