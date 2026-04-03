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
    """Stage an ActivityLog row in the current session.

    The row is not flushed or committed here - the caller must do that as part
    of its own transaction so the activity entry is atomic with the change it
    describes.  `user_id` may be `None` for system-initiated actions.
    `old_value` / `new_value` store display names, not IDs, so the log
    remains accurate even if names are later changed.
    """
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
    """Apply a partial update to a task and log each meaningful field change.

    Only fields present in the original request body (i.e. not excluded by
    `exclude_unset`) are processed.  Each supported field has dedicated
    handling:

    - `status_id`: logs old/new status *names* (not IDs) for readability.
    - `priority`: normalises enum to its `.value` string before comparing.
    - `assignee_ids`: computes the diff against current assignees, removes
      departing ones via `db.delete`, and adds arriving ones as new
      `TaskAssignee` rows.  An activity entry is written for each individual
      add/remove rather than a single bulk event.
    - All other fields: set directly via `setattr`; a title change is also
      logged.

    The caller is responsible for committing and re-fetching the task
    afterward so that relationships (e.g. `task.assignees`) reflect the
    updated state in the response.
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
