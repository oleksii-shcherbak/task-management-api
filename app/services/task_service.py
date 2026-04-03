from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.activity_log import ActivityLog
from app.models.task import Task, TaskPriority
from app.models.task_assignee import TaskAssignee
from app.models.task_status import TaskStatus
from app.models.user import User
from app.schemas.task import TaskUpdate


def log_activity(
    db: AsyncSession,
    *,
    project_id: int,
    user_id: int | None,
    action: str,
    task_id: int | None = None,
    old_value: str | None = None,
    new_value: str | None = None,
) -> None:
    """Add an ActivityLog row to the session. Caller is responsible for committing."""
    db.add(
        ActivityLog(
            project_id=project_id,
            task_id=task_id,
            user_id=user_id,
            action=action,
            old_value=old_value,
            new_value=new_value,
        )
    )


async def update_task(
    db: AsyncSession,
    task: Task,
    body: TaskUpdate,
    current_user: User,
    new_status: TaskStatus | None,
) -> None:
    """
    Apply updates from TaskUpdate to the task and log each meaningful change.
    The caller is responsible for committing and re-fetching the task afterward.
    """
    updates = body.model_dump(exclude_unset=True)

    for field, new_value in updates.items():
        if field == "status_id" and new_status is not None:
            if new_value != task.status_id:
                log_activity(
                    db,
                    project_id=task.project_id,
                    task_id=task.id,
                    user_id=current_user.id,
                    action="status_changed",
                    old_value=task.status.name,
                    new_value=new_status.name,
                )
            task.status_id = new_value

        elif field == "priority":
            old = task.priority.value if task.priority else None
            new = new_value.value if isinstance(new_value, TaskPriority) else new_value
            if old != new:
                log_activity(
                    db,
                    project_id=task.project_id,
                    task_id=task.id,
                    user_id=current_user.id,
                    action="priority_changed",
                    old_value=old,
                    new_value=new,
                )
            task.priority = new_value

        elif field == "assignee_ids":
            new_ids = set(new_value) if new_value else set()
            old_ids = {ta.user_id for ta in task.task_assignees}

            to_remove = old_ids - new_ids
            to_add = new_ids - old_ids

            for ta in list(task.task_assignees):
                if ta.user_id in to_remove:
                    log_activity(
                        db,
                        project_id=task.project_id,
                        task_id=task.id,
                        user_id=current_user.id,
                        action="assignee_removed",
                        old_value=ta.user.name if ta.user else None,
                    )
                    await db.delete(ta)

            if to_add:
                result = await db.execute(select(User).where(User.id.in_(to_add)))
                new_users: dict[int, User] = {u.id: u for u in result.scalars().all()}
                for uid in to_add:
                    db.add(
                        TaskAssignee(
                            task_id=task.id,
                            user_id=uid,
                            assigned_by_id=current_user.id,
                        )
                    )
                    user: User | None = new_users.get(uid)
                    log_activity(
                        db,
                        project_id=task.project_id,
                        task_id=task.id,
                        user_id=current_user.id,
                        action="assignee_added",
                        new_value=user.name if user else str(uid),
                    )

        else:
            if field == "title" and new_value != getattr(task, field):
                log_activity(
                    db,
                    project_id=task.project_id,
                    task_id=task.id,
                    user_id=current_user.id,
                    action="title_changed",
                    old_value=task.title,
                    new_value=new_value,
                )
            setattr(task, field, new_value)
