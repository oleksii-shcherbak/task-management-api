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
    tokens = (await client.post("/api/v1/auth/register", json=user)).json()
    access_token = tokens["access_token"]
    me = await client.get(
        "/api/v1/users/me", headers={"Authorization": f"Bearer {access_token}"}
    )
    return access_token, me.json()["id"]


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
    assert len(data["items"]) == 2
    # Ordered by created_at asc
    assert data["items"][0]["content"] == "First"
    assert data["items"][1]["content"] == "Second"


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
        json={"assignee_ids": [bob_id]},
        headers=auth_headers(alice_token),
    )

    response = await client.get(
        f"/api/v1/tasks/{task['id']}/activity",
        headers=auth_headers(alice_token),
    )

    logs = response.json()
    assignee_log = next(log for log in logs if log["action"] == "assignee_added")
    assert assignee_log["old_value"] is None  # task was created unassigned
    assert assignee_log["new_value"] == "Bob"


# --- Pagination ---


@pytest.mark.asyncio
async def test_list_comments_pagination(client: AsyncClient):
    token, _ = await register_and_login(client, USER_ALICE)
    project = await create_project(client, token)
    task = await create_task(client, token, project["id"])

    for i in range(3):
        await add_comment(client, token, project["id"], task["id"], f"Comment {i}")

    response = await client.get(
        f"/api/v1/projects/{project['id']}/tasks/{task['id']}/comments",
        params={"limit": 2},
        headers=auth_headers(token),
    )
    assert response.status_code == 200
    page1 = response.json()
    assert len(page1["items"]) == 2
    assert page1["has_more"] is True
    assert page1["next_cursor"] is not None

    response2 = await client.get(
        f"/api/v1/projects/{project['id']}/tasks/{task['id']}/comments",
        params={"limit": 2, "cursor": page1["next_cursor"]},
        headers=auth_headers(token),
    )
    assert response2.status_code == 200
    page2 = response2.json()
    assert len(page2["items"]) == 1
    assert page2["has_more"] is False
    assert page2["next_cursor"] is None

    ids1 = {c["id"] for c in page1["items"]}
    ids2 = {c["id"] for c in page2["items"]}
    assert ids1.isdisjoint(ids2)
    assert len(ids1 | ids2) == 3


@pytest.mark.asyncio
async def test_list_comments_invalid_cursor_returns_422(client: AsyncClient):
    token, _ = await register_and_login(client, USER_ALICE)
    project = await create_project(client, token)
    task = await create_task(client, token, project["id"])

    response = await client.get(
        f"/api/v1/projects/{project['id']}/tasks/{task['id']}/comments",
        params={"cursor": "not-valid-base64!!!"},
        headers=auth_headers(token),
    )
    assert response.status_code == 422


# --- @Mentions ---


@pytest.mark.asyncio
async def test_mention_in_comment_resolved_and_stored(client: AsyncClient):
    token_alice, _ = await register_and_login(
        client, {**USER_ALICE, "username": "alice_handle"}
    )
    _token_bob, bob_id = await register_and_login(
        client, {**USER_BOB, "username": "bob_handle"}
    )
    project = await create_project(client, token_alice)
    task = await create_task(client, token_alice, project["id"])

    # Add Bob to project
    await client.post(
        f"/api/v1/projects/{project['id']}/members",
        headers=auth_headers(token_alice),
        json={"user_id": bob_id},
    )

    response = await client.post(
        f"/api/v1/projects/{project['id']}/tasks/{task['id']}/comments",
        headers=auth_headers(token_alice),
        json={"content": "Hey @bob_handle, can you check this?"},
    )
    assert response.status_code == 201
    data = response.json()
    assert len(data["mentions"]) == 1
    assert data["mentions"][0]["username"] == "bob_handle"
    assert data["mentions"][0]["full_name"] == USER_BOB["name"]


@pytest.mark.asyncio
async def test_mention_non_member_is_filtered_out(client: AsyncClient):
    token_alice, _ = await register_and_login(
        client, {**USER_ALICE, "username": "alice_handle"}
    )
    await register_and_login(client, {**USER_BOB, "username": "bob_handle"})
    project = await create_project(client, token_alice)
    task = await create_task(client, token_alice, project["id"])

    # Bob is NOT added to the project
    response = await client.post(
        f"/api/v1/projects/{project['id']}/tasks/{task['id']}/comments",
        headers=auth_headers(token_alice),
        json={"content": "Hey @bob_handle!"},
    )
    assert response.status_code == 201
    assert response.json()["mentions"] == []


@pytest.mark.asyncio
async def test_self_mention_is_filtered_out(client: AsyncClient):
    token_alice, _ = await register_and_login(
        client, {**USER_ALICE, "username": "alice_handle"}
    )
    project = await create_project(client, token_alice)
    task = await create_task(client, token_alice, project["id"])

    response = await client.post(
        f"/api/v1/projects/{project['id']}/tasks/{task['id']}/comments",
        headers=auth_headers(token_alice),
        json={"content": "I did @alice_handle this myself."},
    )
    assert response.status_code == 201
    assert response.json()["mentions"] == []


@pytest.mark.asyncio
async def test_edit_comment_diff_adds_new_mention(client: AsyncClient, arq_mock):
    token_alice, _ = await register_and_login(
        client, {**USER_ALICE, "username": "alice_handle"}
    )
    _token_bob, bob_id = await register_and_login(
        client, {**USER_BOB, "username": "bob_handle"}
    )
    project = await create_project(client, token_alice)
    task = await create_task(client, token_alice, project["id"])
    await client.post(
        f"/api/v1/projects/{project['id']}/members",
        headers=auth_headers(token_alice),
        json={"user_id": bob_id},
    )

    # Create comment without mention
    comment = (
        await client.post(
            f"/api/v1/projects/{project['id']}/tasks/{task['id']}/comments",
            headers=auth_headers(token_alice),
            json={"content": "No mention yet."},
        )
    ).json()

    arq_mock.enqueue_job.reset_mock()

    # Edit to add mention
    response = await client.patch(
        f"/api/v1/comments/{comment['id']}",
        headers=auth_headers(token_alice),
        json={"content": "Now mentioning @bob_handle."},
    )
    assert response.status_code == 200
    assert len(response.json()["mentions"]) == 1

    arq_mock.enqueue_job.assert_called_once_with(
        "send_mention_notification",
        user_id=bob_id,
        actor_name=USER_ALICE["name"],
        source_type="comment",
        source_id=comment["id"],
        body_excerpt="Now mentioning @bob_handle.",
    )


@pytest.mark.asyncio
async def test_edit_comment_with_existing_mention_does_not_re_notify(
    client: AsyncClient, arq_mock
):
    token_alice, _ = await register_and_login(
        client, {**USER_ALICE, "username": "alice_handle"}
    )
    _token_bob, bob_id = await register_and_login(
        client, {**USER_BOB, "username": "bob_handle"}
    )
    project = await create_project(client, token_alice)
    task = await create_task(client, token_alice, project["id"])
    await client.post(
        f"/api/v1/projects/{project['id']}/members",
        headers=auth_headers(token_alice),
        json={"user_id": bob_id},
    )

    # Create comment with an existing mention
    comment = (
        await client.post(
            f"/api/v1/projects/{project['id']}/tasks/{task['id']}/comments",
            headers=auth_headers(token_alice),
            json={"content": "Hey @bob_handle!"},
        )
    ).json()
    assert len(comment["mentions"]) == 1

    arq_mock.enqueue_job.reset_mock()

    # Edit keeping the same mention - should not re-notify
    response = await client.patch(
        f"/api/v1/comments/{comment['id']}",
        headers=auth_headers(token_alice),
        json={"content": "Hey @bob_handle, updated text."},
    )
    assert response.status_code == 200
    assert len(response.json()["mentions"]) == 1
    arq_mock.enqueue_job.assert_not_called()
