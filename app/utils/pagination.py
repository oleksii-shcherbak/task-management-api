import base64
import json

from pydantic import BaseModel


def encode_cursor(data: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(data).encode()).decode()


def decode_cursor(cursor: str) -> dict | None:
    try:
        return json.loads(base64.urlsafe_b64decode(cursor.encode()))
    except Exception:
        return None


class CursorPage[T](BaseModel):
    items: list[T]
    next_cursor: str | None
    has_more: bool
