from typing import ClassVar

from arq import cron
from arq.connections import RedisSettings

from app.config import settings
from app.database import AsyncSessionLocal
from app.tasks.email_tasks import (
    send_assignment_notification,
    send_due_date_reminder,
    send_mention_notification,
    send_password_reset_email,
    send_project_invitation,
    send_status_change_notification,
    send_verification_email,
)
from app.tasks.reminder_tasks import send_due_date_reminders


async def startup(ctx: dict) -> None:
    ctx["db_factory"] = AsyncSessionLocal
    ctx["smtp_host"] = settings.SMTP_HOST
    ctx["smtp_port"] = settings.SMTP_PORT
    ctx["smtp_user"] = settings.SMTP_USER
    ctx["smtp_password"] = settings.SMTP_PASSWORD
    ctx["from_email"] = settings.FROM_EMAIL
    ctx["frontend_url"] = settings.FRONTEND_URL


async def shutdown(_ctx: dict) -> None:
    pass


class WorkerSettings:
    redis_settings = RedisSettings.from_dsn(settings.REDIS_URL)
    functions: ClassVar[list] = [
        send_verification_email,
        send_password_reset_email,
        send_due_date_reminder,
        send_assignment_notification,
        send_project_invitation,
        send_status_change_notification,
        send_mention_notification,
    ]
    cron_jobs: ClassVar[list] = [
        cron(send_due_date_reminders, minute=0),
    ]
    on_startup = startup
    on_shutdown = shutdown
    max_jobs = 10
    job_timeout = 300
