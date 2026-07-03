from __future__ import annotations

import hashlib

from app.core.config import get_settings


def chat_cache_key(user_id: str, message: str) -> str:
    raw = f"{user_id}:{message}"
    return "chat_cache:" + hashlib.sha256(raw.encode()).hexdigest()


async def redis_get(key: str) -> str | None:
    try:
        import redis.asyncio as aioredis

        settings = get_settings()
        url = settings.upstash_redis_url or settings.redis_url
        client = aioredis.from_url(url, decode_responses=True, socket_connect_timeout=2)
        try:
            return await client.get(key)
        finally:
            await client.aclose()
    except Exception:
        return None


async def redis_setex(key: str, ttl: int, value: str) -> None:
    try:
        import redis.asyncio as aioredis

        settings = get_settings()
        url = settings.upstash_redis_url or settings.redis_url
        client = aioredis.from_url(url, decode_responses=True, socket_connect_timeout=2)
        try:
            await client.setex(key, ttl, value)
        finally:
            await client.aclose()
    except Exception:
        return
