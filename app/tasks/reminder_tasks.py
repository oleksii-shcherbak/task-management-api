from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.models.task import Task

logger = structlog.get_logger()


async def send_due_date_reminders(ctx: dict) -> None:
    now = datetime.now(UTC)
    window_end = now + timedelta(hours=24)

    async with ctx["db_factory"]() as db:
        result = await db.execute(
            select(Task)
            .options(
                selectinload(Task.task_assignees),
                selectinload(Task.project),
            )
            .where(
                Task.due_date.is_not(None),
                Task.due_date >= now,
                Task.due_date <= window_end,
                Task.reminder_sent_at.is_(None),
                Task.deleted_at.is_(None),
            )
        )
        tasks = result.scalars().all()

        if not tasks:
            return

        # Extract notification data before closing the session
        notification_data = [
            {
                "task_id": task.id,
                "task_title": task.title,
                "project_name": task.project.name,
                "due_date": task.due_date.strftime("%B %-d, %Y"),
                "assignee_ids": [ta.user_id for ta in task.task_assignees],
            }
            for task in tasks
        ]

        for task in tasks:
            task.reminder_sent_at = now

        await db.commit()

    for data in notification_data:
        for user_id in data["assignee_ids"]:
            await ctx["redis"].enqueue_job(
                "send_due_date_reminder",
                user_id=user_id,
                task_id=data["task_id"],
                task_title=data["task_title"],
                project_name=data["project_name"],
                due_date=data["due_date"],
            )

    logger.info("due_date_reminders_enqueued", task_count=len(notification_data))
