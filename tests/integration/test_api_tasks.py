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
async def test_create_task_with_assignees(client: AsyncClient):
    alice_token = await register_and_login(client, USER_ALICE)
    await register_and_login(client, USER_BOB)
    project = await create_project(client, alice_token)
    await add_member(client, alice_token, project["id"], user_id=2)

    task = await create_task(client, alice_token, project["id"], assignee_ids=[2])

    assert isinstance(task["assignees"], list)
    assert len(task["assignees"]) == 1
    assert task["assignees"][0]["id"] == 2


@pytest.mark.asyncio
async def test_create_task_assignee_not_member_forbidden(client: AsyncClient):
    alice_token = await register_and_login(client, USER_ALICE)
    await register_and_login(client, USER_BOB)  # Bob exists but is not in project
    project = await create_project(client, alice_token)

    response = await client.post(
        f"/api/v1/projects/{project['id']}/tasks",
        json={"title": "Task", "assignee_ids": [2]},
        headers=auth_headers(alice_token),
    )
    assert response.status_code == 403


# --- List Tasks ---


@pytest.mark.asyncio
async def test_list_tasks_only_shows_project_tasks(client: AsyncClient):
    token = await register_and_login(client, USER_ALICE)
    project_a = await create_project(client, token)
    project_b = await create_project(client, token)

    await create_task(client, token, project_a["id"], title="Task in A")
    await create_task(client, token, project_a["id"], title="Task in A 2")
    await create_task(client, token, project_b["id"], title="Task in B")

    response = await client.get(
        f"/api/v1/projects/{project_a['id']}/tasks",
        headers=auth_headers(token),
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2
    assert all(t["project_id"] == project_a["id"] for t in data)


@pytest.mark.asyncio
async def test_list_tasks_filter_by_status(client: AsyncClient):
    token = await register_and_login(client, USER_ALICE)
    project = await create_project(client, token)
    statuses = await get_statuses(client, token, project["id"])

    await create_task(client, token, project["id"], title="Backlog Task")
    await create_task(
        client,
        token,
        project["id"],
        title="In Progress Task",
        status_id=statuses["In Progress"]["id"],
    )

    response = await client.get(
        f"/api/v1/projects/{project['id']}/tasks",
        params={"status_id": statuses["Backlog"]["id"]},
        headers=auth_headers(token),
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["title"] == "Backlog Task"


@pytest.mark.asyncio
async def test_list_tasks_filter_by_priority(client: AsyncClient):
    token = await register_and_login(client, USER_ALICE)
    project = await create_project(client, token)

    await create_task(client, token, project["id"], title="High", priority="high")
    await create_task(client, token, project["id"], title="Low", priority="low")
    await create_task(client, token, project["id"], title="No Priority")

    response = await client.get(
        f"/api/v1/projects/{project['id']}/tasks",
        params={"priority": "high"},
        headers=auth_headers(token),
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["title"] == "High"


@pytest.mark.asyncio
async def test_list_tasks_non_member_forbidden(client: AsyncClient):
    alice_token = await register_and_login(client, USER_ALICE)
    bob_token = await register_and_login(client, USER_BOB)
    project = await create_project(client, alice_token)

    response = await client.get(
        f"/api/v1/projects/{project['id']}/tasks",
        headers=auth_headers(bob_token),
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_list_tasks_excludes_deleted(client: AsyncClient):
    token = await register_and_login(client, USER_ALICE)
    project = await create_project(client, token)

    task = await create_task(client, token, project["id"], title="To Delete")
    await client.delete(f"/api/v1/tasks/{task['id']}", headers=auth_headers(token))

    response = await client.get(
        f"/api/v1/projects/{project['id']}/tasks",
        headers=auth_headers(token),
    )
    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_list_tasks_ordered_by_status_then_position(client: AsyncClient):
    token = await register_and_login(client, USER_ALICE)
    project = await create_project(client, token)
    statuses = await get_statuses(client, token, project["id"])

    t1 = await create_task(client, token, project["id"], title="Backlog 1")
    t2 = await create_task(
        client,
        token,
        project["id"],
        title="In Progress 1",
        status_id=statuses["In Progress"]["id"],
    )
    t3 = await create_task(client, token, project["id"], title="Backlog 2")

    response = await client.get(
        f"/api/v1/projects/{project['id']}/tasks",
        headers=auth_headers(token),
    )
    data = response.json()
    ids = [t["id"] for t in data]

    # Both Backlog tasks must appear before the In Progress task
    assert ids.index(t1["id"]) < ids.index(t2["id"])
    assert ids.index(t3["id"]) < ids.index(t2["id"])


# --- Get Task ---


@pytest.mark.asyncio
async def test_get_task_embeds_status(client: AsyncClient):
    token = await register_and_login(client, USER_ALICE)
    project = await create_project(client, token)
    task = await create_task(client, token, project["id"])

    response = await client.get(
        f"/api/v1/tasks/{task['id']}",
        headers=auth_headers(token),
    )
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data["status"], dict)
    assert "id" in data["status"]
    assert "name" in data["status"]
    assert "type" in data["status"]
    assert "color" in data["status"]


@pytest.mark.asyncio
async def test_get_task_non_member_forbidden(client: AsyncClient):
    alice_token = await register_and_login(client, USER_ALICE)
    bob_token = await register_and_login(client, USER_BOB)
    project = await create_project(client, alice_token)
    task = await create_task(client, alice_token, project["id"])

    response = await client.get(
        f"/api/v1/tasks/{task['id']}",
        headers=auth_headers(bob_token),
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_get_deleted_task_returns_404(client: AsyncClient):
    token = await register_and_login(client, USER_ALICE)
    project = await create_project(client, token)
    task = await create_task(client, token, project["id"])

    await client.delete(f"/api/v1/tasks/{task['id']}", headers=auth_headers(token))

    response = await client.get(
        f"/api/v1/tasks/{task['id']}",
        headers=auth_headers(token),
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_get_nonexistent_task_returns_404(client: AsyncClient):
    token = await register_and_login(client, USER_ALICE)

    response = await client.get("/api/v1/tasks/99999", headers=auth_headers(token))
    assert response.status_code == 404


# --- Update Task ---


@pytest.mark.asyncio
async def test_update_task_owner_can_update_all_fields(client: AsyncClient):
    token = await register_and_login(client, USER_ALICE)
    project = await create_project(client, token)
    task = await create_task(client, token, project["id"])

    response = await client.patch(
        f"/api/v1/tasks/{task['id']}",
        json={
            "title": "Updated Title",
            "description": "New desc",
            "priority": "urgent",
        },
        headers=auth_headers(token),
    )
    assert response.status_code == 200
    data = response.json()
    assert data["title"] == "Updated Title"
    assert data["description"] == "New desc"
    assert data["priority"] == "urgent"


@pytest.mark.asyncio
async def test_update_task_status_appends_to_end_of_new_column(client: AsyncClient):
    token = await register_and_login(client, USER_ALICE)
    project = await create_project(client, token)
    statuses = await get_statuses(client, token, project["id"])
    in_progress_id = statuses["In Progress"]["id"]

    # Create 2 tasks already in In Progress - they should get positions 1 and 2
    await create_task(
        client, token, project["id"], title="IP 1", status_id=in_progress_id
    )
    await create_task(
        client, token, project["id"], title="IP 2", status_id=in_progress_id
    )
    # Create a new task in Backlog and move it to In Progress - it should get position 3
    task = await create_task(client, token, project["id"], title="Backlog Task")

    response = await client.patch(
        f"/api/v1/tasks/{task['id']}",
        json={"status_id": in_progress_id},
        headers=auth_headers(token),
    )
    assert response.status_code == 200
    assert response.json()["position"] == 3
    assert response.json()["status"]["name"] == "In Progress"


@pytest.mark.asyncio
async def test_update_task_member_can_update_own_assigned_task(client: AsyncClient):
    alice_token = await register_and_login(client, USER_ALICE)
    bob_token = await register_and_login(client, USER_BOB)
    project = await create_project(client, alice_token)
    await add_member(client, alice_token, project["id"], user_id=2, role="member")
    statuses = await get_statuses(client, alice_token, project["id"])

    task = await create_task(
        client, alice_token, project["id"], title="Bob's Task", assignee_ids=[2]
    )

    response = await client.patch(
        f"/api/v1/tasks/{task['id']}",
        json={
            "status_id": statuses["In Progress"]["id"],
            "description": "Working on it",
        },
        headers=auth_headers(bob_token),
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"]["name"] == "In Progress"
    assert data["description"] == "Working on it"


@pytest.mark.asyncio
async def test_update_task_member_cannot_update_unassigned_task(client: AsyncClient):
    alice_token = await register_and_login(client, USER_ALICE)
    bob_token = await register_and_login(client, USER_BOB)
    project = await create_project(client, alice_token)
    await add_member(client, alice_token, project["id"], user_id=2, role="member")

    task = await create_task(client, alice_token, project["id"], title="Unassigned")

    response = await client.patch(
        f"/api/v1/tasks/{task['id']}",
        json={"description": "Bob trying to update"},
        headers=auth_headers(bob_token),
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_update_task_member_cannot_update_forbidden_fields(client: AsyncClient):
    alice_token = await register_and_login(client, USER_ALICE)
    bob_token = await register_and_login(client, USER_BOB)
    project = await create_project(client, alice_token)
    await add_member(client, alice_token, project["id"], user_id=2, role="member")

    task = await create_task(
        client, alice_token, project["id"], title="Task", assignee_ids=[2]
    )

    response = await client.patch(
        f"/api/v1/tasks/{task['id']}",
        json={"title": "Bob's rename attempt"},
        headers=auth_headers(bob_token),
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_update_task_invalid_status_id_returns_404(client: AsyncClient):
    alice_token = await register_and_login(client, USER_ALICE)
    project_a = await create_project(client, alice_token)
    project_b = await create_project(client, alice_token)
    statuses_b = await get_statuses(client, alice_token, project_b["id"])

    task = await create_task(client, alice_token, project_a["id"])

    response = await client.patch(
        f"/api/v1/tasks/{task['id']}",
        json={"status_id": statuses_b["Backlog"]["id"]},
        headers=auth_headers(alice_token),
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_update_task_null_status_id_returns_422(client: AsyncClient):
    token = await register_and_login(client, USER_ALICE)
    project = await create_project(client, token)
    task = await create_task(client, token, project["id"])

    response = await client.patch(
        f"/api/v1/tasks/{task['id']}",
        json={"status_id": None},
        headers=auth_headers(token),
    )
    assert response.status_code == 422


# --- Delete Task ---


@pytest.mark.asyncio
async def test_delete_task_soft_deletes(client: AsyncClient):
    token = await register_and_login(client, USER_ALICE)
    project = await create_project(client, token)
    task = await create_task(client, token, project["id"])

    delete_response = await client.delete(
        f"/api/v1/tasks/{task['id']}", headers=auth_headers(token)
    )
    assert delete_response.status_code == 204

    list_response = await client.get(
        f"/api/v1/projects/{project['id']}/tasks", headers=auth_headers(token)
    )
    assert list_response.json() == []

    get_response = await client.get(
        f"/api/v1/tasks/{task['id']}", headers=auth_headers(token)
    )
    assert get_response.status_code == 404


@pytest.mark.asyncio
async def test_delete_task_member_forbidden(client: AsyncClient):
    alice_token = await register_and_login(client, USER_ALICE)
    bob_token = await register_and_login(client, USER_BOB)
    project = await create_project(client, alice_token)
    await add_member(client, alice_token, project["id"], user_id=2, role="member")

    task = await create_task(client, alice_token, project["id"])

    response = await client.delete(
        f"/api/v1/tasks/{task['id']}", headers=auth_headers(bob_token)
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_delete_nonexistent_task_returns_404(client: AsyncClient):
    token = await register_and_login(client, USER_ALICE)

    response = await client.delete("/api/v1/tasks/99999", headers=auth_headers(token))
    assert response.status_code == 404


# --- Reorder Task ---


@pytest.mark.asyncio
async def test_reorder_task_move_up_same_column(client: AsyncClient):
    token = await register_and_login(client, USER_ALICE)
    project = await create_project(client, token)
    statuses = await get_statuses(client, token, project["id"])
    backlog_id = statuses["Backlog"]["id"]

    t1 = await create_task(client, token, project["id"], title="A")  # pos 1
    t2 = await create_task(client, token, project["id"], title="B")  # pos 2
    t3 = await create_task(client, token, project["id"], title="C")  # pos 3
    t4 = await create_task(client, token, project["id"], title="D")  # pos 4

    # Move D (pos 4) → pos 1
    response = await client.patch(
        f"/api/v1/tasks/{t4['id']}/position",
        json={"status_id": backlog_id, "position": 1},
        headers=auth_headers(token),
    )
    assert response.status_code == 200
    assert response.json()["position"] == 1

    tasks = (
        await client.get(
            f"/api/v1/projects/{project['id']}/tasks",
            params={"status_id": backlog_id},
            headers=auth_headers(token),
        )
    ).json()
    positions = {t["id"]: t["position"] for t in tasks}
    assert positions[t4["id"]] == 1
    assert positions[t1["id"]] == 2
    assert positions[t2["id"]] == 3
    assert positions[t3["id"]] == 4


@pytest.mark.asyncio
async def test_reorder_task_move_down_same_column(client: AsyncClient):
    token = await register_and_login(client, USER_ALICE)
    project = await create_project(client, token)
    statuses = await get_statuses(client, token, project["id"])
    backlog_id = statuses["Backlog"]["id"]

    t1 = await create_task(client, token, project["id"], title="A")  # pos 1
    t2 = await create_task(client, token, project["id"], title="B")  # pos 2
    t3 = await create_task(client, token, project["id"], title="C")  # pos 3
    t4 = await create_task(client, token, project["id"], title="D")  # pos 4

    # Move A (pos 1) → pos 3
    response = await client.patch(
        f"/api/v1/tasks/{t1['id']}/position",
        json={"status_id": backlog_id, "position": 3},
        headers=auth_headers(token),
    )
    assert response.status_code == 200
    assert response.json()["position"] == 3

    tasks = (
        await client.get(
            f"/api/v1/projects/{project['id']}/tasks",
            params={"status_id": backlog_id},
            headers=auth_headers(token),
        )
    ).json()
    positions = {t["id"]: t["position"] for t in tasks}
    assert positions[t2["id"]] == 1
    assert positions[t3["id"]] == 2
    assert positions[t1["id"]] == 3
    assert positions[t4["id"]] == 4


@pytest.mark.asyncio
async def test_reorder_task_different_column(client: AsyncClient):
    token = await register_and_login(client, USER_ALICE)
    project = await create_project(client, token)
    statuses = await get_statuses(client, token, project["id"])
    backlog_id = statuses["Backlog"]["id"]
    in_progress_id = statuses["In Progress"]["id"]

    t1 = await create_task(client, token, project["id"], title="A")  # Backlog pos 1
    t2 = await create_task(client, token, project["id"], title="B")  # Backlog pos 2
    t3 = await create_task(client, token, project["id"], title="C")  # Backlog pos 3
    t4 = await create_task(
        client, token, project["id"], title="D", status_id=in_progress_id
    )  # In Progress pos 1

    # Move C from Backlog pos 3 → In Progress pos 1
    response = await client.patch(
        f"/api/v1/tasks/{t3['id']}/position",
        json={"status_id": in_progress_id, "position": 1},
        headers=auth_headers(token),
    )
    assert response.status_code == 200
    assert response.json()["status"]["name"] == "In Progress"
    assert response.json()["position"] == 1

    # Backlog gap closed: A=1, B=2
    backlog = (
        await client.get(
            f"/api/v1/projects/{project['id']}/tasks",
            params={"status_id": backlog_id},
            headers=auth_headers(token),
        )
    ).json()
    bp = {t["id"]: t["position"] for t in backlog}
    assert len(backlog) == 2
    assert bp[t1["id"]] == 1
    assert bp[t2["id"]] == 2

    # In Progress room made: C=1, D=2
    ip = (
        await client.get(
            f"/api/v1/projects/{project['id']}/tasks",
            params={"status_id": in_progress_id},
            headers=auth_headers(token),
        )
    ).json()
    ipp = {t["id"]: t["position"] for t in ip}
    assert len(ip) == 2
    assert ipp[t3["id"]] == 1
    assert ipp[t4["id"]] == 2


@pytest.mark.asyncio
async def test_reorder_task_clamp_to_max(client: AsyncClient):
    token = await register_and_login(client, USER_ALICE)
    project = await create_project(client, token)
    statuses = await get_statuses(client, token, project["id"])
    backlog_id = statuses["Backlog"]["id"]

    t1 = await create_task(client, token, project["id"], title="A")
    await create_task(client, token, project["id"], title="B")
    await create_task(client, token, project["id"], title="C")

    # Request pos 99, should clamp to 3
    response = await client.patch(
        f"/api/v1/tasks/{t1['id']}/position",
        json={"status_id": backlog_id, "position": 99},
        headers=auth_headers(token),
    )
    assert response.status_code == 200
    assert response.json()["position"] == 3


@pytest.mark.asyncio
async def test_reorder_task_noop(client: AsyncClient):
    token = await register_and_login(client, USER_ALICE)
    project = await create_project(client, token)
    statuses = await get_statuses(client, token, project["id"])

    task = await create_task(client, token, project["id"], title="A")  # pos 1

    response = await client.patch(
        f"/api/v1/tasks/{task['id']}/position",
        json={"status_id": statuses["Backlog"]["id"], "position": 1},
        headers=auth_headers(token),
    )
    assert response.status_code == 200
    assert response.json()["position"] == 1


@pytest.mark.asyncio
async def test_reorder_task_member_forbidden(client: AsyncClient):
    alice_token = await register_and_login(client, USER_ALICE)
    bob_token = await register_and_login(client, USER_BOB)
    project = await create_project(client, alice_token)
    await add_member(client, alice_token, project["id"], user_id=2, role="member")
    statuses = await get_statuses(client, alice_token, project["id"])

    task = await create_task(client, alice_token, project["id"])

    response = await client.patch(
        f"/api/v1/tasks/{task['id']}/position",
        json={"status_id": statuses["Backlog"]["id"], "position": 1},
        headers=auth_headers(bob_token),
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_reorder_task_invalid_status_returns_404(client: AsyncClient):
    alice_token = await register_and_login(client, USER_ALICE)
    project_a = await create_project(client, alice_token)
    project_b = await create_project(client, alice_token)
    statuses_b = await get_statuses(client, alice_token, project_b["id"])

    task = await create_task(client, alice_token, project_a["id"])

    response = await client.patch(
        f"/api/v1/tasks/{task['id']}/position",
        json={"status_id": statuses_b["Backlog"]["id"], "position": 1},
        headers=auth_headers(alice_token),
    )
    assert response.status_code == 404


# --- Assignees ---


@pytest.mark.asyncio
async def test_list_tasks_filter_by_assignee(client: AsyncClient):
    alice_token = await register_and_login(client, USER_ALICE)
    await register_and_login(client, USER_BOB)
    project = await create_project(client, alice_token)
    await add_member(client, alice_token, project["id"], user_id=2)

    await create_task(
        client, alice_token, project["id"], title="Assigned to Bob", assignee_ids=[2]
    )
    await create_task(client, alice_token, project["id"], title="Unassigned")

    response = await client.get(
        f"/api/v1/projects/{project['id']}/tasks",
        params={"assignee_id": 2},
        headers=auth_headers(alice_token),
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["title"] == "Assigned to Bob"


@pytest.mark.asyncio
async def test_update_task_add_and_remove_assignees(client: AsyncClient):
    alice_token = await register_and_login(client, USER_ALICE)
    await register_and_login(client, USER_BOB)
    project = await create_project(client, alice_token)
    await add_member(client, alice_token, project["id"], user_id=2)

    task = await create_task(client, alice_token, project["id"], assignee_ids=[2])
    assert len(task["assignees"]) == 1

    # Remove Bob, add Alice (user id=1)
    response = await client.patch(
        f"/api/v1/tasks/{task['id']}",
        json={"assignee_ids": [1]},
        headers=auth_headers(alice_token),
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data["assignees"]) == 1
    assert data["assignees"][0]["id"] == 1


@pytest.mark.asyncio
async def test_update_task_clear_all_assignees(client: AsyncClient):
    alice_token = await register_and_login(client, USER_ALICE)
    await register_and_login(client, USER_BOB)
    project = await create_project(client, alice_token)
    await add_member(client, alice_token, project["id"], user_id=2)

    task = await create_task(client, alice_token, project["id"], assignee_ids=[2])
    assert len(task["assignees"]) == 1

    response = await client.patch(
        f"/api/v1/tasks/{task['id']}",
        json={"assignee_ids": []},
        headers=auth_headers(alice_token),
    )
    assert response.status_code == 200
    assert response.json()["assignees"] == []


@pytest.mark.asyncio
async def test_update_task_assignee_not_member_forbidden(client: AsyncClient):
    alice_token = await register_and_login(client, USER_ALICE)
    await register_and_login(client, USER_BOB)  # Bob exists but not in project
    project = await create_project(client, alice_token)
    task = await create_task(client, alice_token, project["id"])

    response = await client.patch(
        f"/api/v1/tasks/{task['id']}",
        json={"assignee_ids": [2]},
        headers=auth_headers(alice_token),
    )
    assert response.status_code == 403
