from __future__ import annotations

import hashlib


def chat_cache_key(user_id: str, message: str) -> str:
    raw = f"{user_id}:{message}"
    return "chat_cache:" + hashlib.sha256(raw.encode()).hexdigest()


async def redis_get(key: str) -> str | None:
    try:
        from app.db.redis_client import get_redis

        client = await get_redis()
        if client is None:
            return None
        return await client.get(key)
    except Exception:
        return None


async def redis_setex(key: str, ttl: int, value: str) -> None:
    try:
        from app.db.redis_client import get_redis

        client = await get_redis()
        if client is None:
            return
        await client.setex(key, ttl, value)
    except Exception:
        return
