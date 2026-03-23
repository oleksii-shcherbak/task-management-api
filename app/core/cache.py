from collections.abc import AsyncGenerator

from redis.asyncio import Redis

_state: dict[str, Redis | None] = {"client": None}


async def init_redis(url: str) -> None:
    _state["client"] = Redis.from_url(url, decode_responses=True)


async def close_redis() -> None:
    client = _state["client"]
    if client is not None:
        await client.aclose()
        _state["client"] = None


async def get_redis() -> AsyncGenerator[Redis, None]:
    client = _state["client"]
    if client is None:
        raise RuntimeError("Redis client is not initialized. Call init_redis() first.")
    yield client
