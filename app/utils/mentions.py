import re
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.project_member import ProjectMember
from app.models.user import User
from app.models.username_history import UsernameHistory

_MENTION_RE = re.compile(r"@([a-z0-9_-]{3,30})")


def parse_mentioned_usernames(text: str | None) -> set[str]:
    return set(_MENTION_RE.findall(text or ""))


async def resolve_mention_user_ids(
    usernames: set[str],
    project_id: int,
    exclude_user_id: int,
    db: AsyncSession,
) -> set[int]:
    """Resolve a set of @mentioned usernames to user IDs.

    Rules applied:
    - Only users who are current project members are returned.
    - The actor (exclude_user_id) is excluded from the result.
    - Usernames that recently changed are resolved via username_history
      so that a mention of an old handle still reaches the right person
      during the 30-day reservation window.
    """
    if not usernames:
        return set()

    # Primary lookup: current active username
    active_result = await db.execute(
        select(User.id, User.username).where(
            User.username.in_(usernames),
            User.deleted_at.is_(None),
        )
    )
    found: dict[str, int] = {row.username: row.id for row in active_result}

    # For any username not resolved above, check history within grace period
    unresolved = usernames - set(found.keys())
    if unresolved:
        history_result = await db.execute(
            select(UsernameHistory.old_username, UsernameHistory.user_id)
            .join(User, User.id == UsernameHistory.user_id)
            .where(
                UsernameHistory.old_username.in_(unresolved),
                UsernameHistory.released_at > datetime.now(UTC),
                User.deleted_at.is_(None),
            )
        )
        # history may return multiple rows per username - latest wins (handled by dict update)
        for row in history_result:
            found.setdefault(row.old_username, row.user_id)

    if not found:
        return set()

    candidate_ids = set(found.values()) - {exclude_user_id}
    if not candidate_ids:
        return set()

    # Scope to project members only
    member_result = await db.execute(
        select(ProjectMember.user_id).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id.in_(candidate_ids),
        )
    )
    return {row.user_id for row in member_result}
