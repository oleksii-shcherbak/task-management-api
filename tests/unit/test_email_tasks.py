from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.user import User
from app.tasks.email_tasks import (
    send_assignment_notification,
    send_due_date_reminder,
    send_mention_notification,
    send_password_reset_email,
    send_project_invitation,
    send_status_change_notification,
    send_verification_email,
)


@pytest_asyncio.fixture
async def alice(db_session: AsyncSession) -> User:
    user = User(
        email="alice@example.com",
        name="Alice",
        username="alice",
        is_active=True,
        password_changed_at=datetime.now(UTC),
    )
    db_session.add(user)
    await db_session.commit()
    return user


@pytest.fixture
def base_ctx(engine):
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    return {
        "db_factory": session_factory,
        "smtp_host": None,
        "smtp_port": 587,
        "smtp_user": "",
        "smtp_password": "",
        "from_email": "noreply@test.com",
        "frontend_url": "http://localhost:8000",
    }


# --- Verification Email ---


@pytest.mark.asyncio
async def test_send_verification_email_skips_smtp_when_no_host(alice, base_ctx):
    await send_verification_email(base_ctx, user_id=alice.id, token="tok123")


@pytest.mark.asyncio
async def test_send_verification_email_user_not_found_returns_silently(base_ctx):
    await send_verification_email(base_ctx, user_id=99999, token="tok123")


@pytest.mark.asyncio
async def test_send_verification_email_sends_via_smtp(alice, base_ctx):
    base_ctx["smtp_host"] = "smtp.example.com"

    with patch("aiosmtplib.send", new_callable=AsyncMock) as mock_send:
        await send_verification_email(base_ctx, user_id=alice.id, token="tok123")

    mock_send.assert_called_once()
    _, kwargs = mock_send.call_args
    assert kwargs["hostname"] == "smtp.example.com"
    assert kwargs["port"] == 587


@pytest.mark.asyncio
async def test_send_verification_email_includes_smtp_credentials(alice, base_ctx):
    base_ctx["smtp_host"] = "smtp.example.com"
    base_ctx["smtp_user"] = "user@example.com"
    base_ctx["smtp_password"] = "secret"

    with patch("aiosmtplib.send", new_callable=AsyncMock) as mock_send:
        await send_verification_email(base_ctx, user_id=alice.id, token="tok123")

    _, kwargs = mock_send.call_args
    assert kwargs["username"] == "user@example.com"
    assert kwargs["password"] == "secret"


# --- Password Reset Email ---


@pytest.mark.asyncio
async def test_send_password_reset_email_skips_smtp_when_no_host(alice, base_ctx):
    await send_password_reset_email(base_ctx, user_id=alice.id, token="reset-tok")


@pytest.mark.asyncio
async def test_send_password_reset_email_user_not_found_returns_silently(base_ctx):
    await send_password_reset_email(base_ctx, user_id=99999, token="reset-tok")


# --- Due Date Reminder ---


@pytest.mark.asyncio
async def test_send_due_date_reminder_skips_smtp_when_no_host(alice, base_ctx):
    await send_due_date_reminder(
        base_ctx,
        user_id=alice.id,
        task_id=1,
        task_title="Fix bug",
        project_name="My Project",
        due_date="2026-04-10",
    )


@pytest.mark.asyncio
async def test_send_due_date_reminder_user_not_found_returns_silently(base_ctx):
    await send_due_date_reminder(
        base_ctx,
        user_id=99999,
        task_id=1,
        task_title="Fix bug",
        project_name="My Project",
        due_date="2026-04-10",
    )


# --- Project Invitation ---


@pytest.mark.asyncio
async def test_send_project_invitation_skips_smtp_when_no_host(alice, base_ctx):
    await send_project_invitation(
        base_ctx, user_id=alice.id, project_name="My Project", role="member"
    )


@pytest.mark.asyncio
async def test_send_project_invitation_user_not_found_returns_silently(base_ctx):
    await send_project_invitation(
        base_ctx, user_id=99999, project_name="My Project", role="member"
    )


# --- Status Change Notification ---


@pytest.mark.asyncio
async def test_send_status_change_notification_skips_smtp_when_no_host(alice, base_ctx):
    await send_status_change_notification(
        base_ctx,
        user_id=alice.id,
        task_id=1,
        task_title="Fix bug",
        project_name="My Project",
        old_status="Backlog",
        new_status="In Progress",
    )


@pytest.mark.asyncio
async def test_send_status_change_notification_user_not_found_returns_silently(
    base_ctx,
):
    await send_status_change_notification(
        base_ctx,
        user_id=99999,
        task_id=1,
        task_title="Fix bug",
        project_name="My Project",
        old_status="Backlog",
        new_status="In Progress",
    )


# --- Assignment Notification ---


@pytest.mark.asyncio
async def test_send_assignment_notification_skips_smtp_when_no_host(alice, base_ctx):
    await send_assignment_notification(
        base_ctx,
        user_id=alice.id,
        task_id=1,
        task_title="Fix bug",
        project_name="My Project",
    )


@pytest.mark.asyncio
async def test_send_assignment_notification_user_not_found_returns_silently(base_ctx):
    await send_assignment_notification(
        base_ctx,
        user_id=99999,
        task_id=1,
        task_title="Fix bug",
        project_name="My Project",
    )


# --- Mention Notification ---


@pytest.mark.asyncio
async def test_send_mention_notification_comment_source(alice, base_ctx):
    await send_mention_notification(
        base_ctx,
        user_id=alice.id,
        actor_name="Bob",
        source_type="comment",
        source_id=1,
        body_excerpt="Hey @alice check this out",
    )


@pytest.mark.asyncio
async def test_send_mention_notification_task_source(alice, base_ctx):
    await send_mention_notification(
        base_ctx,
        user_id=alice.id,
        actor_name="Bob",
        source_type="task",
        source_id=5,
        body_excerpt="@alice please review",
    )


@pytest.mark.asyncio
async def test_send_mention_notification_user_not_found_returns_silently(base_ctx):
    await send_mention_notification(
        base_ctx,
        user_id=99999,
        actor_name="Bob",
        source_type="comment",
        source_id=1,
        body_excerpt="Hey @ghost",
    )
