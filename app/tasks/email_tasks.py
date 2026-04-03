from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

import aiosmtplib
import structlog
from sqlalchemy import select

from app.models.user import User
from app.tasks.email_templates import (
    assignment_notification_email,
    due_date_reminder_email,
    mention_notification_email,
    password_reset_email,
    project_invitation_email,
    status_change_notification_email,
    verification_email,
)

logger = structlog.get_logger()


async def _send_smtp(ctx: dict[Any, Any], *, to: str, subject: str, html: str) -> None:
    if not ctx.get("smtp_host"):
        logger.info("smtp_skipped_no_host", to=to, subject=subject)
        return

    message = MIMEMultipart("alternative")
    message["From"] = ctx["from_email"]
    message["To"] = to
    message["Subject"] = subject
    message.attach(MIMEText(html, "html"))

    kwargs: dict[str, Any] = {
        "hostname": ctx["smtp_host"],
        "port": ctx["smtp_port"],
        "start_tls": True,
    }
    if ctx.get("smtp_user"):
        kwargs["username"] = ctx["smtp_user"]
        kwargs["password"] = ctx["smtp_password"]

    await aiosmtplib.send(message, **kwargs)


async def send_verification_email(
    ctx: dict[Any, Any], *, user_id: int, token: str
) -> None:
    async with ctx["db_factory"]() as db:
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()

    if user is None:
        return

    verify_url = f"{ctx['frontend_url']}/api/v1/auth/verify-email?token={token}"
    html = verification_email(user.name, verify_url)
    await _send_smtp(ctx, to=user.email, subject="Verify your email address", html=html)
    logger.info("verification_email_sent", user_id=user_id)


async def send_password_reset_email(
    ctx: dict[Any, Any], *, user_id: int, token: str
) -> None:
    async with ctx["db_factory"]() as db:
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()

    if user is None:
        return

    html = password_reset_email(user.name, token)
    await _send_smtp(ctx, to=user.email, subject="Reset your password", html=html)
    logger.info("password_reset_email_sent", user_id=user_id)


async def send_due_date_reminder(
    ctx: dict[Any, Any],
    *,
    user_id: int,
    task_id: int,
    task_title: str,
    project_name: str,
    due_date: str,
) -> None:
    async with ctx["db_factory"]() as db:
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()

    if user is None:
        return

    html = due_date_reminder_email(user.name, task_title, project_name, due_date)
    await _send_smtp(
        ctx, to=user.email, subject=f"Reminder: {task_title} is due soon", html=html
    )
    logger.info("due_date_reminder_sent", user_id=user_id, task_id=task_id)


async def send_project_invitation(
    ctx: dict[Any, Any], *, user_id: int, project_name: str, role: str
) -> None:
    async with ctx["db_factory"]() as db:
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()

    if user is None:
        return

    html = project_invitation_email(user.name, project_name, role)
    await _send_smtp(
        ctx,
        to=user.email,
        subject=f"You were added to project: {project_name}",
        html=html,
    )
    logger.info("project_invitation_sent", user_id=user_id, project_name=project_name)


async def send_status_change_notification(
    ctx: dict[Any, Any],
    *,
    user_id: int,
    task_id: int,
    task_title: str,
    project_name: str,
    old_status: str,
    new_status: str,
) -> None:
    async with ctx["db_factory"]() as db:
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()

    if user is None:
        return

    html = status_change_notification_email(
        user.name, task_title, project_name, old_status, new_status
    )
    await _send_smtp(
        ctx,
        to=user.email,
        subject=f"Status changed: {task_title}",
        html=html,
    )
    logger.info(
        "status_change_notification_sent",
        user_id=user_id,
        task_id=task_id,
        old_status=old_status,
        new_status=new_status,
    )


async def send_assignment_notification(
    ctx: dict[Any, Any],
    *,
    user_id: int,
    task_id: int,
    task_title: str,
    project_name: str,
) -> None:
    async with ctx["db_factory"]() as db:
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()

    if user is None:
        return

    html = assignment_notification_email(user.name, task_title, project_name)
    await _send_smtp(
        ctx,
        to=user.email,
        subject=f"You were assigned to: {task_title}",
        html=html,
    )
    logger.info("assignment_notification_sent", user_id=user_id, task_id=task_id)


async def send_mention_notification(
    ctx: dict[Any, Any],
    *,
    user_id: int,
    actor_name: str,
    source_type: str,
    source_id: int,
    body_excerpt: str,
) -> None:
    async with ctx["db_factory"]() as db:
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()

    if user is None:
        return

    html = mention_notification_email(user.name, actor_name, source_type, body_excerpt)
    await _send_smtp(
        ctx,
        to=user.email,
        subject=f"{actor_name} mentioned you",
        html=html,
    )
    logger.info(
        "mention_notification_sent",
        user_id=user_id,
        source_type=source_type,
        source_id=source_id,
    )
