import base64
import json
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession


def encode_cursor(data: dict) -> str:
    """Serialize a cursor payload dict to a URL-safe base64 string."""
    return base64.urlsafe_b64encode(json.dumps(data).encode()).decode()


def decode_cursor(cursor: str) -> dict | None:
    """Decode a cursor string produced by `encode_cursor`.

    Returns `None` rather than raising if the value is missing, truncated,
    or not valid base64/JSON - all treated as "start from the beginning".
    """
    try:
        return json.loads(base64.urlsafe_b64decode(cursor.encode()))
    except ValueError:
        return None


class CursorPage[T](BaseModel):
    """Generic cursor-paginated response envelope."""

    items: list[T]
    next_cursor: str | None
    has_more: bool


async def paginate_query[T](
    db: AsyncSession,
    query: Any,
    limit: int,
    build_cursor: Callable[[T], dict[str, Any]],
) -> CursorPage[T]:
    """Execute a query with cursor pagination.

    Applies limit + 1 internally to detect whether more pages exist.
    build_cursor receives the last item and returns a dict suitable for encode_cursor.
    """
    result = await db.execute(query.limit(limit + 1))
    items: list[T] = list(result.scalars().all())
    has_more = len(items) > limit
    if has_more:
        items = items[:limit]
    next_cursor = encode_cursor(build_cursor(items[-1])) if has_more else None
    return CursorPage(items=items, next_cursor=next_cursor, has_more=has_more)
