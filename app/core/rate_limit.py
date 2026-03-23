import time
import uuid

from fastapi import Depends, Request
from redis.asyncio import Redis

from app.core.cache import get_redis
from app.core.exceptions import RateLimitError


class RateLimiter:
    """Sliding window rate limiter backed by a Redis sorted set.

    Each request adds a timestamped entry to the set. Entries older than
    `window` seconds are pruned before counting, which gives an accurate
    count of requests within the rolling window rather than a fixed bucket.
    The entire check-and-increment is wrapped in a MULTI/EXEC pipeline so
    it is atomic - no other client can interleave between the prune and the
    count.
    """

    def __init__(self, limit: int, window: int) -> None:
        self.limit = limit
        self.window = window  # seconds

    async def __call__(
        self,
        request: Request,
        redis: Redis = Depends(get_redis),
    ) -> None:
        ip = request.client.host if request.client else "unknown"
        key = f"rate:{request.url.path}:{ip}"
        now = time.time()

        async with redis.pipeline(transaction=True) as pipe:
            pipe.zremrangebyscore(key, 0, now - self.window)
            # UUID suffix prevents member collision when requests share the same timestamp
            pipe.zadd(key, {f"{now}:{uuid.uuid4().hex}": now})
            pipe.zcard(key)
            pipe.expire(key, self.window)
            results = await pipe.execute()

        count = results[2]  # zcard result - index matches command order above
        if count > self.limit:
            raise RateLimitError(
                f"Rate limit exceeded. Try again in {self.window} seconds.",
                retry_after=self.window,
            )
