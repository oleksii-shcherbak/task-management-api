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


async def register_and_login(client: AsyncClient, user: dict) -> tuple[str, int]:
    """Register a user and return (access_token, user_id)."""
    reg = await client.post("/api/v1/auth/register", json=user)
    user_id = reg.json()["user"]["id"]
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": user["email"], "password": user["password"]},
    )
    return response.json()["access_token"], user_id


def auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def create_project(client: AsyncClient, token: str) -> dict:
    response = await client.post(
        "/api/v1/projects",
        json={"name": "Test Project"},
        headers=auth_headers(token),
    )
    assert response.status_code == 201
    return response.json()


async def create_task(client: AsyncClient, token: str, project_id: int) -> dict:
    response = await client.post(
        f"/api/v1/projects/{project_id}/tasks",
        json={"title": "Test Task"},
        headers=auth_headers(token),
    )
    assert response.status_code == 201
    return response.json()


async def add_member(
    client: AsyncClient, token: str, project_id: int, user_id: int, role: str = "member"
) -> None:
    response = await client.post(
        f"/api/v1/projects/{project_id}/members",
        json={"user_id": user_id, "role": role},
        headers=auth_headers(token),
    )
    assert response.status_code == 201


async def add_comment(
    client: AsyncClient,
    token: str,
    project_id: int,
    task_id: int,
    content: str = "Hello",
) -> dict:
    response = await client.post(
        f"/api/v1/projects/{project_id}/tasks/{task_id}/comments",
        json={"content": content},
        headers=auth_headers(token),
    )
    assert response.status_code == 201
    return response.json()


# --- Comment tests ---


@pytest.mark.asyncio
async def test_add_comment_success(client: AsyncClient):
    token, _ = await register_and_login(client, USER_ALICE)
    project = await create_project(client, token)
    task = await create_task(client, token, project["id"])

    response = await client.post(
        f"/api/v1/projects/{project['id']}/tasks/{task['id']}/comments",
        json={"content": "Great task!"},
        headers=auth_headers(token),
    )

    assert response.status_code == 201
    data = response.json()
    assert data["content"] == "Great task!"
    assert data["task_id"] == task["id"]
    assert data["author"]["name"] == "Alice"
    assert data["edited_at"] is None


@pytest.mark.asyncio
async def test_add_comment_empty_content_rejected(client: AsyncClient):
    token, _ = await register_and_login(client, USER_ALICE)
    project = await create_project(client, token)
    task = await create_task(client, token, project["id"])

    response = await client.post(
        f"/api/v1/projects/{project['id']}/tasks/{task['id']}/comments",
        json={"content": ""},
        headers=auth_headers(token),
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_add_comment_requires_project_membership(client: AsyncClient):
    alice_token, _ = await register_and_login(client, USER_ALICE)
    bob_token, _ = await register_and_login(client, USER_BOB)
    project = await create_project(client, alice_token)
    task = await create_task(client, alice_token, project["id"])

    # Bob is not a member
    response = await client.post(
        f"/api/v1/projects/{project['id']}/tasks/{task['id']}/comments",
        json={"content": "I'm not a member"},
        headers=auth_headers(bob_token),
    )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_list_comments(client: AsyncClient):
    token, _ = await register_and_login(client, USER_ALICE)
    project = await create_project(client, token)
    task = await create_task(client, token, project["id"])

    await add_comment(client, token, project["id"], task["id"], "First")
    await add_comment(client, token, project["id"], task["id"], "Second")

    response = await client.get(
        f"/api/v1/projects/{project['id']}/tasks/{task['id']}/comments",
        headers=auth_headers(token),
    )

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    # Ordered by created_at asc
    assert data[0]["content"] == "First"
    assert data[1]["content"] == "Second"


@pytest.mark.asyncio
async def test_edit_comment_success(client: AsyncClient):
    token, _ = await register_and_login(client, USER_ALICE)
    project = await create_project(client, token)
    task = await create_task(client, token, project["id"])
    comment = await add_comment(client, token, project["id"], task["id"])

    response = await client.patch(
        f"/api/v1/comments/{comment['id']}",
        json={"content": "Updated content"},
        headers=auth_headers(token),
    )

    assert response.status_code == 200
    data = response.json()
    assert data["content"] == "Updated content"
    assert data["edited_at"] is not None


@pytest.mark.asyncio
async def test_edit_comment_only_author_can_edit(client: AsyncClient):
    alice_token, _ = await register_and_login(client, USER_ALICE)
    bob_token, bob_id = await register_and_login(client, USER_BOB)
    project = await create_project(client, alice_token)
    task = await create_task(client, alice_token, project["id"])

    await add_member(client, alice_token, project["id"], bob_id)
    comment = await add_comment(client, alice_token, project["id"], task["id"])

    # Bob tries to edit Alice's comment
    response = await client.patch(
        f"/api/v1/comments/{comment['id']}",
        json={"content": "Bob edited this"},
        headers=auth_headers(bob_token),
    )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_delete_comment_by_author(client: AsyncClient):
    # Tests that a regular member (not owner/manager) can delete their own comment
    alice_token, _ = await register_and_login(client, USER_ALICE)
    bob_token, bob_id = await register_and_login(client, USER_BOB)
    project = await create_project(client, alice_token)
    task = await create_task(client, alice_token, project["id"])

    await add_member(client, alice_token, project["id"], bob_id)
    comment = await add_comment(client, bob_token, project["id"], task["id"])

    response = await client.delete(
        f"/api/v1/comments/{comment['id']}",
        headers=auth_headers(bob_token),
    )

    assert response.status_code == 204


@pytest.mark.asyncio
async def test_delete_comment_by_manager(client: AsyncClient):
    alice_token, _ = await register_and_login(client, USER_ALICE)
    bob_token, bob_id = await register_and_login(client, USER_BOB)
    project = await create_project(client, alice_token)
    task = await create_task(client, alice_token, project["id"])

    await add_member(client, alice_token, project["id"], bob_id, role="manager")

    # Bob (manager) posts a comment, Alice (owner) deletes it
    comment = await add_comment(client, bob_token, project["id"], task["id"])

    response = await client.delete(
        f"/api/v1/comments/{comment['id']}",
        headers=auth_headers(alice_token),
    )

    assert response.status_code == 204


@pytest.mark.asyncio
async def test_member_cannot_delete_others_comment(client: AsyncClient):
    alice_token, _ = await register_and_login(client, USER_ALICE)
    bob_token, bob_id = await register_and_login(client, USER_BOB)
    project = await create_project(client, alice_token)
    task = await create_task(client, alice_token, project["id"])

    await add_member(client, alice_token, project["id"], bob_id)
    comment = await add_comment(client, alice_token, project["id"], task["id"])

    # Bob (member) tries to delete Alice's comment
    response = await client.delete(
        f"/api/v1/comments/{comment['id']}",
        headers=auth_headers(bob_token),
    )

    assert response.status_code == 403


# --- Activity log tests ---


@pytest.mark.asyncio
async def test_task_created_activity_logged(client: AsyncClient):
    token, _ = await register_and_login(client, USER_ALICE)
    project = await create_project(client, token)
    task = await create_task(client, token, project["id"])

    response = await client.get(
        f"/api/v1/tasks/{task['id']}/activity",
        headers=auth_headers(token),
    )

    assert response.status_code == 200
    logs = response.json()
    assert len(logs) == 1
    assert logs[0]["action"] == "task_created"
    assert logs[0]["new_value"] == "Test Task"
    assert logs[0]["old_value"] is None
    assert logs[0]["actor"]["name"] == "Alice"


@pytest.mark.asyncio
async def test_status_change_activity_logged(client: AsyncClient):
    token, _ = await register_and_login(client, USER_ALICE)
    project = await create_project(client, token)
    task = await create_task(client, token, project["id"])

    statuses = await client.get(
        f"/api/v1/projects/{project['id']}/statuses",
        headers=auth_headers(token),
    )
    status_map = {s["name"]: s for s in statuses.json()}

    await client.patch(
        f"/api/v1/tasks/{task['id']}",
        json={"status_id": status_map["In Progress"]["id"]},
        headers=auth_headers(token),
    )

    response = await client.get(
        f"/api/v1/tasks/{task['id']}/activity",
        headers=auth_headers(token),
    )

    logs = response.json()
    status_log = next(log for log in logs if log["action"] == "status_changed")
    assert status_log["old_value"] == "Backlog"
    assert status_log["new_value"] == "In Progress"


@pytest.mark.asyncio
async def test_activity_requires_project_membership(client: AsyncClient):
    alice_token, _ = await register_and_login(client, USER_ALICE)
    bob_token, _ = await register_and_login(client, USER_BOB)
    project = await create_project(client, alice_token)
    task = await create_task(client, alice_token, project["id"])

    response = await client.get(
        f"/api/v1/tasks/{task['id']}/activity",
        headers=auth_headers(bob_token),
    )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_comment_on_deleted_task_returns_404(client: AsyncClient):
    token, _ = await register_and_login(client, USER_ALICE)
    project = await create_project(client, token)
    task = await create_task(client, token, project["id"])

    # Soft delete the task
    await client.delete(
        f"/api/v1/tasks/{task['id']}",
        headers=auth_headers(token),
    )

    response = await client.post(
        f"/api/v1/projects/{project['id']}/tasks/{task['id']}/comments",
        json={"content": "Comment on deleted task"},
        headers=auth_headers(token),
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_unauthenticated_cannot_add_comment(client: AsyncClient):
    token, _ = await register_and_login(client, USER_ALICE)
    project = await create_project(client, token)
    task = await create_task(client, token, project["id"])

    response = await client.post(
        f"/api/v1/projects/{project['id']}/tasks/{task['id']}/comments",
        json={"content": "No token"},
    )

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_priority_change_activity_logged(client: AsyncClient):
    token, _ = await register_and_login(client, USER_ALICE)
    project = await create_project(client, token)
    task = await create_task(client, token, project["id"])

    await client.patch(
        f"/api/v1/tasks/{task['id']}",
        json={"priority": "urgent"},
        headers=auth_headers(token),
    )

    response = await client.get(
        f"/api/v1/tasks/{task['id']}/activity",
        headers=auth_headers(token),
    )

    logs = response.json()
    priority_log = next(log for log in logs if log["action"] == "priority_changed")
    assert priority_log["old_value"] is None  # task was created with no priority
    assert priority_log["new_value"] == "urgent"


@pytest.mark.asyncio
async def test_assignee_change_activity_logged(client: AsyncClient):
    alice_token, _ = await register_and_login(client, USER_ALICE)
    _, bob_id = await register_and_login(client, USER_BOB)
    project = await create_project(client, alice_token)
    task = await create_task(client, alice_token, project["id"])

    await add_member(client, alice_token, project["id"], bob_id)

    await client.patch(
        f"/api/v1/tasks/{task['id']}",
        json={"assignee_id": bob_id},
        headers=auth_headers(alice_token),
    )

    response = await client.get(
        f"/api/v1/tasks/{task['id']}/activity",
        headers=auth_headers(alice_token),
    )

    logs = response.json()
    assignee_log = next(log for log in logs if log["action"] == "assignee_changed")
    assert assignee_log["old_value"] is None  # task was created unassigned
    assert assignee_log["new_value"] == "Bob"
