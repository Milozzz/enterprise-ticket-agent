"""Small Redis-backed idempotency helpers with DB-safe fallback paths."""

from __future__ import annotations

import hashlib

from app.core.logging import get_logger
from app.db.redis_client import get_redis

logger = get_logger(__name__)


def stable_idempotency_key(namespace: str, *parts: object) -> str:
    raw = ":".join(str(part or "") for part in parts)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return f"idempotency:{namespace}:{digest}"


async def acquire_idempotency_key(key: str, ttl_seconds: int = 86400) -> bool:
    """
    Return True when this caller owns the key.

    Redis outages should not break the business path; DB uniqueness checks remain
    the final guard for operations that persist side effects.
    """
    try:
        redis = await get_redis()
        if redis is None:
            return True
        acquired = await redis.set(key, "1", ex=ttl_seconds, nx=True)
        return bool(acquired)
    except Exception as e:
        logger.warning("idempotency_redis_unavailable", key=key, error=str(e))
        return True


async def release_idempotency_key(key: str) -> None:
    try:
        redis = await get_redis()
        if redis is not None:
            await redis.delete(key)
    except Exception as e:
        logger.warning("idempotency_redis_release_failed", key=key, error=str(e))
