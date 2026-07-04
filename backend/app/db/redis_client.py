import redis.asyncio as aioredis
from app.core.config import get_settings

_redis_client: aioredis.Redis | None = None


def _redis_url() -> str:
    settings = get_settings()
    return settings.upstash_redis_url or settings.redis_url


async def get_redis() -> aioredis.Redis | None:
    """Return a shared, pooled async Redis client (or None when unavailable).

    The client is created once and reused (redis-py maintains an internal
    connection pool), so hot paths must not build a new connection per call.
    A failed connect returns None without caching it, so a later call retries
    once Redis comes back.
    """
    global _redis_client
    if _redis_client is not None:
        return _redis_client
    try:
        client = aioredis.from_url(
            _redis_url(),
            encoding="utf-8",
            decode_responses=True,
            socket_connect_timeout=2,
        )
        await client.ping()
    except Exception:
        # 开发环境无 Redis 时降级为 None，不影响 Agent 核心功能（不缓存 None，便于恢复后重连）
        return None
    _redis_client = client
    return _redis_client


async def close_redis() -> None:
    global _redis_client
    if _redis_client:
        await _redis_client.aclose()
        _redis_client = None
