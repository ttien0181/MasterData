"""Optional Redis progress reporting; no AI cache is included."""

from __future__ import annotations


class RedisProgress:
    def __init__(self, redis_url: str, ttl_seconds: int = 86400):
        import redis

        self.client = redis.Redis.from_url(redis_url, decode_responses=True)
        self.ttl_seconds = ttl_seconds

    def update(self, job_id: str, **values: str | int | float) -> None:
        key = f"job:{job_id}:progress"
        self.client.hset(key, mapping={name: str(value) for name, value in values.items()})
        self.client.expire(key, self.ttl_seconds)
