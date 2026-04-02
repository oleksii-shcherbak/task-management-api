import pytest
from httpx import AsyncClient

USER_ALICE = {
    "email": "alice@example.com",
    "password": "securepassword123",
    "name": "Alice",
}
USER_BOB = {"email": "bob@example.com", "password": "securepassword123", "name": "Bob"}

# Minimal byte sequences that satisfy filetype's magic byte detection
JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 32
PDF_BYTES = b"%PDF-1.4\n" + b"\x00" * 32
EXE_BYTES = b"MZ" + b"\x00" * 32  # Windows PE - not in allowlist


# --- Helpers ---


async def register_and_login(client: AsyncClient, user: dict) -> tuple[str, int]:
    tokens = (await client.post("/api/v1/auth/register", json=user)).json()
    token = tokens["access_token"]
    me = (await client.get("/api/v1/users/me", headers=auth(token))).json()
    return token, me["id"]


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def create_project(client: AsyncClient, token: str) -> dict:
    r = await client.post(
        "/api/v1/projects", json={"name": "Test Project"}, headers=auth(token)
    )
    assert r.status_code == 201
    return r.json()


async def create_task(client: AsyncClient, token: str, project_id: int) -> dict:
    r = await client.post(
        f"/api/v1/projects/{project_id}/tasks",
        json={"title": "Test Task"},
        headers=auth(token),
    )
    assert r.status_code == 201
    return r.json()


async def add_member(
    client: AsyncClient, token: str, project_id: int, user_id: int
) -> None:
    r = await client.post(
        f"/api/v1/projects/{project_id}/members",
        json={"user_id": user_id, "role": "member"},
        headers=auth(token),
    )
    assert r.status_code == 201


async def upload_attachment(
    client: AsyncClient,
    token: str,
    task_id: int,
    data: bytes = JPEG_BYTES,
    name: str = "test.jpg",
) -> dict:
    r = await client.post(
        f"/api/v1/tasks/{task_id}/attachments",
        files={"file": (name, data, "application/octet-stream")},
        headers=auth(token),
    )
    assert r.status_code == 201
    return r.json()


# --- Upload tests ---


@pytest.mark.asyncio
async def test_upload_attachment_success(client: AsyncClient):
    token, _ = await register_and_login(client, USER_ALICE)
    project = await create_project(client, token)
    task = await create_task(client, token, project["id"])

    r = await client.post(
        f"/api/v1/tasks/{task['id']}/attachments",
        files={"file": ("report.jpg", JPEG_BYTES, "application/octet-stream")},
        headers=auth(token),
    )

    assert r.status_code == 201
    body = r.json()
    assert body["filename"] == "report.jpg"
    assert body["mime_type"] == "image/jpeg"
    assert body["size_bytes"] == len(JPEG_BYTES)
    assert body["task_id"] == task["id"]
    assert "url" in body


@pytest.mark.asyncio
async def test_upload_attachment_pdf(client: AsyncClient):
    token, _ = await register_and_login(client, USER_ALICE)
    project = await create_project(client, token)
    task = await create_task(client, token, project["id"])

    r = await client.post(
        f"/api/v1/tasks/{task['id']}/attachments",
        files={"file": ("doc.pdf", PDF_BYTES, "application/octet-stream")},
        headers=auth(token),
    )

    assert r.status_code == 201
    assert r.json()["mime_type"] == "application/pdf"


@pytest.mark.asyncio
async def test_upload_attachment_disallowed_type(client: AsyncClient):
    token, _ = await register_and_login(client, USER_ALICE)
    project = await create_project(client, token)
    task = await create_task(client, token, project["id"])

    r = await client.post(
        f"/api/v1/tasks/{task['id']}/attachments",
        files={"file": ("malware.exe", EXE_BYTES, "application/octet-stream")},
        headers=auth(token),
    )

    assert r.status_code == 422


@pytest.mark.asyncio
async def test_upload_attachment_oversized(client: AsyncClient):
    token, _ = await register_and_login(client, USER_ALICE)
    project = await create_project(client, token)
    task = await create_task(client, token, project["id"])

    oversized = JPEG_BYTES + b"\x00" * (10 * 1024 * 1024)  # just over 10 MB

    r = await client.post(
        f"/api/v1/tasks/{task['id']}/attachments",
        files={"file": ("big.jpg", oversized, "application/octet-stream")},
        headers=auth(token),
    )

    assert r.status_code == 422


@pytest.mark.asyncio
async def test_upload_attachment_non_member_forbidden(client: AsyncClient):
    alice_token, _ = await register_and_login(client, USER_ALICE)
    bob_token, _ = await register_and_login(client, USER_BOB)
    project = await create_project(client, alice_token)
    task = await create_task(client, alice_token, project["id"])

    r = await client.post(
        f"/api/v1/tasks/{task['id']}/attachments",
        files={"file": ("test.jpg", JPEG_BYTES, "application/octet-stream")},
        headers=auth(bob_token),
    )

    assert r.status_code == 403


@pytest.mark.asyncio
async def test_upload_attachment_task_not_found(client: AsyncClient):
    token, _ = await register_and_login(client, USER_ALICE)

    r = await client.post(
        "/api/v1/tasks/99999/attachments",
        files={"file": ("test.jpg", JPEG_BYTES, "application/octet-stream")},
        headers=auth(token),
    )

    assert r.status_code == 404


# --- List tests ---


@pytest.mark.asyncio
async def test_list_attachments(client: AsyncClient):
    token, _ = await register_and_login(client, USER_ALICE)
    project = await create_project(client, token)
    task = await create_task(client, token, project["id"])

    await upload_attachment(client, token, task["id"], JPEG_BYTES, "a.jpg")
    await upload_attachment(client, token, task["id"], PDF_BYTES, "b.pdf")

    r = await client.get(f"/api/v1/tasks/{task['id']}/attachments", headers=auth(token))

    assert r.status_code == 200
    items = r.json()
    assert len(items) == 2
    assert items[0]["filename"] == "a.jpg"
    assert items[1]["filename"] == "b.pdf"


@pytest.mark.asyncio
async def test_list_attachments_non_member_forbidden(client: AsyncClient):
    alice_token, _ = await register_and_login(client, USER_ALICE)
    bob_token, _ = await register_and_login(client, USER_BOB)
    project = await create_project(client, alice_token)
    task = await create_task(client, alice_token, project["id"])

    r = await client.get(
        f"/api/v1/tasks/{task['id']}/attachments", headers=auth(bob_token)
    )

    assert r.status_code == 403


# --- Get URL tests ---


@pytest.mark.asyncio
async def test_get_attachment_url(client: AsyncClient):
    token, _ = await register_and_login(client, USER_ALICE)
    project = await create_project(client, token)
    task = await create_task(client, token, project["id"])
    attachment = await upload_attachment(client, token, task["id"])

    r = await client.get(
        f"/api/v1/attachments/{attachment['id']}/url", headers=auth(token)
    )

    assert r.status_code == 200
    assert "url" in r.json()


@pytest.mark.asyncio
async def test_get_attachment_url_non_member_forbidden(client: AsyncClient):
    alice_token, _ = await register_and_login(client, USER_ALICE)
    bob_token, _ = await register_and_login(client, USER_BOB)
    project = await create_project(client, alice_token)
    task = await create_task(client, alice_token, project["id"])
    attachment = await upload_attachment(client, alice_token, task["id"])

    r = await client.get(
        f"/api/v1/attachments/{attachment['id']}/url", headers=auth(bob_token)
    )

    assert r.status_code == 403


# --- Delete tests ---


@pytest.mark.asyncio
async def test_delete_attachment_by_uploader(client: AsyncClient):
    alice_token, _ = await register_and_login(client, USER_ALICE)
    bob_token, bob_id = await register_and_login(client, USER_BOB)
    project = await create_project(client, alice_token)
    task = await create_task(client, alice_token, project["id"])
    await add_member(client, alice_token, project["id"], bob_id)

    # Bob uploads then deletes his own attachment
    attachment = await upload_attachment(client, bob_token, task["id"])
    r = await client.delete(
        f"/api/v1/attachments/{attachment['id']}", headers=auth(bob_token)
    )

    assert r.status_code == 204


@pytest.mark.asyncio
async def test_delete_attachment_by_project_owner(client: AsyncClient):
    alice_token, _ = await register_and_login(client, USER_ALICE)
    bob_token, bob_id = await register_and_login(client, USER_BOB)
    project = await create_project(client, alice_token)
    task = await create_task(client, alice_token, project["id"])
    await add_member(client, alice_token, project["id"], bob_id)

    # Bob uploads, Alice (owner) deletes it
    attachment = await upload_attachment(client, bob_token, task["id"])
    r = await client.delete(
        f"/api/v1/attachments/{attachment['id']}", headers=auth(alice_token)
    )

    assert r.status_code == 204


@pytest.mark.asyncio
async def test_delete_attachment_by_non_uploader_member_forbidden(client: AsyncClient):
    alice_token, _ = await register_and_login(client, USER_ALICE)
    bob_token, bob_id = await register_and_login(client, USER_BOB)
    carol = {
        "email": "carol@example.com",
        "password": "securepassword123",
        "name": "Carol",
    }
    carol_token, carol_id = await register_and_login(client, carol)

    project = await create_project(client, alice_token)
    task = await create_task(client, alice_token, project["id"])
    await add_member(client, alice_token, project["id"], bob_id)
    await add_member(client, alice_token, project["id"], carol_id)

    # Bob uploads, Carol (also just a member) tries to delete - should be rejected
    attachment = await upload_attachment(client, bob_token, task["id"])
    r = await client.delete(
        f"/api/v1/attachments/{attachment['id']}", headers=auth(carol_token)
    )

    assert r.status_code == 403


@pytest.mark.asyncio
async def test_delete_attachment_not_found(client: AsyncClient):
    token, _ = await register_and_login(client, USER_ALICE)

    r = await client.delete("/api/v1/attachments/99999", headers=auth(token))

    assert r.status_code == 404


# --- Auth guards ---


@pytest.mark.asyncio
async def test_upload_attachment_requires_auth(client: AsyncClient):
    token, _ = await register_and_login(client, USER_ALICE)
    project = await create_project(client, token)
    task = await create_task(client, token, project["id"])

    r = await client.post(
        f"/api/v1/tasks/{task['id']}/attachments",
        files={"file": ("test.jpg", JPEG_BYTES, "application/octet-stream")},
    )
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_list_attachments_requires_auth(client: AsyncClient):
    token, _ = await register_and_login(client, USER_ALICE)
    project = await create_project(client, token)
    task = await create_task(client, token, project["id"])

    r = await client.get(f"/api/v1/tasks/{task['id']}/attachments")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_list_attachments_task_not_found(client: AsyncClient):
    token, _ = await register_and_login(client, USER_ALICE)

    r = await client.get("/api/v1/tasks/99999/attachments", headers=auth(token))
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_get_attachment_url_requires_auth(client: AsyncClient):
    r = await client.get("/api/v1/attachments/1/url")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_get_attachment_url_not_found(client: AsyncClient):
    token, _ = await register_and_login(client, USER_ALICE)

    r = await client.get("/api/v1/attachments/99999/url", headers=auth(token))
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_delete_attachment_requires_auth(client: AsyncClient):
    r = await client.delete("/api/v1/attachments/1")
    assert r.status_code == 401


# --- MIME detection edge cases ---


@pytest.mark.asyncio
async def test_upload_attachment_txt_file(client: AsyncClient):
    token, _ = await register_and_login(client, USER_ALICE)
    project = await create_project(client, token)
    task = await create_task(client, token, project["id"])

    r = await client.post(
        f"/api/v1/tasks/{task['id']}/attachments",
        files={"file": ("notes.txt", b"hello world", "application/octet-stream")},
        headers=auth(token),
    )
    assert r.status_code == 201
    assert r.json()["mime_type"] == "text/plain"


@pytest.mark.asyncio
async def test_upload_attachment_csv_file(client: AsyncClient):
    token, _ = await register_and_login(client, USER_ALICE)
    project = await create_project(client, token)
    task = await create_task(client, token, project["id"])

    r = await client.post(
        f"/api/v1/tasks/{task['id']}/attachments",
        files={"file": ("data.csv", b"a,b,c\n1,2,3", "application/octet-stream")},
        headers=auth(token),
    )
    assert r.status_code == 201
    assert r.json()["mime_type"] == "text/csv"


@pytest.mark.asyncio
async def test_upload_attachment_unknown_type_rejected(client: AsyncClient):
    token, _ = await register_and_login(client, USER_ALICE)
    project = await create_project(client, token)
    task = await create_task(client, token, project["id"])

    r = await client.post(
        f"/api/v1/tasks/{task['id']}/attachments",
        files={
            "file": (
                "weird.xyz",
                b"random garbage bytes here",
                "application/octet-stream",
            )
        },
        headers=auth(token),
    )
    assert r.status_code == 422
