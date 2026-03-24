from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

_state: dict[str, ArqRedis | None] = {"pool": None}


async def init_arq_pool(redis_url: str) -> None:
    _state["pool"] = await create_pool(RedisSettings.from_dsn(redis_url))


async def close_arq_pool() -> None:
    pool = _state["pool"]
    if pool is not None:
        await pool.aclose()
        _state["pool"] = None


def get_arq_pool() -> ArqRedis:
    pool = _state["pool"]
    if pool is None:
        raise RuntimeError("ARQ pool not initialized")
    return pool
