from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.task import Task
from app.tasks.reminder_tasks import send_due_date_reminders

USER_ALICE = {
    "email": "alice@example.com",
    "password": "securepassword123",
    "name": "Alice",
}


async def register_and_login(client: AsyncClient, user: dict) -> str:
    await client.post("/api/v1/auth/register", json=user)
    response = await client.post(
        "/api/v1/auth/login",
        json={"email": user["email"], "password": user["password"]},
    )
    return response.json()["access_token"]


def auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def make_ctx(test_factory) -> dict:
    mock_redis = MagicMock()
    mock_redis.enqueue_job = AsyncMock()
    return {
        "db_factory": test_factory,
        "redis": mock_redis,
        "smtp_host": "",
        "smtp_port": 587,
        "smtp_user": "",
        "smtp_password": "",
        "from_email": "",
        "frontend_url": "http://localhost:8000",
    }


@pytest.mark.asyncio
async def test_reminders_marks_due_tasks_and_enqueues(
    client: AsyncClient, db_session: AsyncSession, engine
):
    test_factory = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False
    )

    token = await register_and_login(client, USER_ALICE)
    alice_id = (
        await client.get("/api/v1/users/me", headers=auth_headers(token))
    ).json()["id"]

    project_resp = await client.post(
        "/api/v1/projects",
        json={"name": "Test Project"},
        headers=auth_headers(token),
    )
    project_id = project_resp.json()["id"]

    task_resp = await client.post(
        f"/api/v1/projects/{project_id}/tasks",
        json={"title": "Upcoming task", "assignee_ids": [alice_id]},
        headers=auth_headers(token),
    )
    task_id = task_resp.json()["id"]

    result = await db_session.execute(select(Task).where(Task.id == task_id))
    task = result.scalar_one()
    task.due_date = datetime.now(UTC) + timedelta(hours=12)
    await db_session.commit()

    ctx = make_ctx(test_factory)
    await send_due_date_reminders(ctx)

    await db_session.refresh(task)
    assert task.reminder_sent_at is not None
    ctx["redis"].enqueue_job.assert_called_once_with(
        "send_due_date_reminder",
        user_id=alice_id,
        task_id=task_id,
        task_title="Upcoming task",
        project_name="Test Project",
        due_date=task.due_date.strftime("%B %-d, %Y"),
    )


@pytest.mark.asyncio
async def test_reminders_skips_tasks_outside_window(
    client: AsyncClient, db_session: AsyncSession, engine
):
    test_factory = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False
    )

    token = await register_and_login(client, USER_ALICE)
    project_resp = await client.post(
        "/api/v1/projects",
        json={"name": "Test Project"},
        headers=auth_headers(token),
    )
    project_id = project_resp.json()["id"]

    task_resp = await client.post(
        f"/api/v1/projects/{project_id}/tasks",
        json={"title": "Far future task"},
        headers=auth_headers(token),
    )
    task_id = task_resp.json()["id"]

    result = await db_session.execute(select(Task).where(Task.id == task_id))
    task = result.scalar_one()
    task.due_date = datetime.now(UTC) + timedelta(hours=36)
    await db_session.commit()

    ctx = make_ctx(test_factory)
    await send_due_date_reminders(ctx)

    await db_session.refresh(task)
    assert task.reminder_sent_at is None
    ctx["redis"].enqueue_job.assert_not_called()


@pytest.mark.asyncio
async def test_reminders_skips_already_reminded_tasks(
    client: AsyncClient, db_session: AsyncSession, engine
):
    test_factory = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False
    )

    token = await register_and_login(client, USER_ALICE)
    project_resp = await client.post(
        "/api/v1/projects",
        json={"name": "Test Project"},
        headers=auth_headers(token),
    )
    project_id = project_resp.json()["id"]

    task_resp = await client.post(
        f"/api/v1/projects/{project_id}/tasks",
        json={"title": "Already reminded"},
        headers=auth_headers(token),
    )
    task_id = task_resp.json()["id"]

    result = await db_session.execute(select(Task).where(Task.id == task_id))
    task = result.scalar_one()
    task.due_date = datetime.now(UTC) + timedelta(hours=6)
    task.reminder_sent_at = datetime.now(UTC) - timedelta(hours=1)
    await db_session.commit()

    ctx = make_ctx(test_factory)
    await send_due_date_reminders(ctx)

    ctx["redis"].enqueue_job.assert_not_called()
