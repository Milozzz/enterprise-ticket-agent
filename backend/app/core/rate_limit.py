"""
Redis 滑动窗口限流中间件

策略：每个 (user_id, endpoint) 在 60s 窗口内最多 rate_limit_rpm 次请求。
user_id 从 Authorization header 解析（JWT sub），无 token 时用 IP 做 key。

Redis 不可用时静默放行（fail-open），不影响正常流程。
"""

from __future__ import annotations

import time
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

# 仅对这些路径限流（其余路径放行）
_RATE_LIMITED_PREFIXES = ("/api/agent/chat", "/api/agent/resume")


def _extract_user_key(request: Request) -> str:
    """从 JWT Bearer token 提取 user_id，失败时用 IP。"""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        token = auth[7:]
        try:
            from jose import jwt as _jwt
            settings = get_settings()
            payload = _jwt.decode(
                token,
                settings.secret_key,
                algorithms=[settings.jwt_algorithm],
                options={"verify_exp": False},  # 限流时不验过期，只提 sub
            )
            sub = payload.get("sub")
            if sub:
                return f"rl:user:{sub}"
        except Exception:
            pass
    # 无法解析 JWT → 用 IP
    client_ip = request.client.host if request.client else "unknown"
    return f"rl:ip:{client_ip}"


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    滑动计数窗口限流（每分钟 rpm 次）。
    使用 Redis INCR + EXPIRE 实现原子计数。
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        # 只对指定路径限流
        path = request.url.path
        if not any(path.startswith(p) for p in _RATE_LIMITED_PREFIXES):
            return await call_next(request)

        import os
        if os.environ.get("TESTING") == "1":
            return await call_next(request)

        settings = get_settings()
        rpm = settings.rate_limit_rpm
        window = 60  # seconds

        try:
            import redis as _redis
            url = settings.upstash_redis_url or settings.redis_url
            client = _redis.from_url(url, decode_responses=True, socket_connect_timeout=1)

            key = _extract_user_key(request)
            current = client.incr(key)
            if current == 1:
                client.expire(key, window)

            remaining = max(0, rpm - current)
            reset_at = int(time.time()) + window

            if current > rpm:
                logger.warning("rate_limit_exceeded", key=key, count=current, rpm=rpm)
                return Response(
                    content='{"detail":"请求过于频繁，请稍后再试"}',
                    status_code=429,
                    media_type="application/json",
                    headers={
                        "X-RateLimit-Limit": str(rpm),
                        "X-RateLimit-Remaining": "0",
                        "X-RateLimit-Reset": str(reset_at),
                        "Retry-After": str(window),
                    },
                )

            response = await call_next(request)
            response.headers["X-RateLimit-Limit"] = str(rpm)
            response.headers["X-RateLimit-Remaining"] = str(remaining)
            response.headers["X-RateLimit-Reset"] = str(reset_at)
            return response

        except Exception as e:
            # Redis 不可用 → fail-open，放行请求
            logger.warning("rate_limit_redis_unavailable", error=str(e))
            return await call_next(request)
