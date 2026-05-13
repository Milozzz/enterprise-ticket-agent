"""
tests/test_auth_and_ratelimit.py

JWT 认证 + Rate Limiting 测试
"""

import os
import pytest

os.environ["TESTING"] = "1"
os.environ["GOOGLE_API_KEY"] = "test-key"
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./test_auth.db"


# ──────────────────────────────────────────────────────────────────
# JWT 认证测试
# ──────────────────────────────────────────────────────────────────

class TestJWT:
    def test_create_and_decode_token(self):
        from app.core.auth import create_access_token, _decode_token
        token = create_access_token(user_id="42", role="MANAGER")
        assert isinstance(token, str)
        payload = _decode_token(token)
        assert payload["sub"] == "42"
        assert payload["role"] == "MANAGER"

    def test_token_default_role(self):
        from app.core.auth import create_access_token, _decode_token
        token = create_access_token(user_id="7")
        payload = _decode_token(token)
        assert payload["role"] == "USER"

    def test_invalid_token_raises(self):
        from app.core.auth import _decode_token
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            _decode_token("not.a.real.token")
        assert exc_info.value.status_code == 401

    def test_get_optional_user_testing_mode(self):
        """TESTING=1 时 get_optional_user 直接返回 user_id=1"""
        import asyncio
        from app.core.auth import get_optional_user
        result = asyncio.get_event_loop().run_until_complete(
            get_optional_user(credentials=None)
        )
        assert result is not None
        assert result["user_id"] == "1"   # 必须是整数字符串，不是 "dev_user"
        assert result["role"] in ("AGENT", "MANAGER", "USER")

    def test_user_id_is_numeric_string_in_testing_mode(self):
        """user_id 必须能被 int() 转换，否则 UserMemory FK 查询会静默失败"""
        import asyncio
        from app.core.auth import get_optional_user
        result = asyncio.get_event_loop().run_until_complete(
            get_optional_user(credentials=None)
        )
        assert result is not None
        # 这是核心断言：保证 _try_int(user_id) 不返回 None
        assert int(result["user_id"]) >= 0

    def test_integer_user_id_in_token(self):
        """create_access_token 接受整型 user_id，sub 字段应为字符串"""
        from app.core.auth import create_access_token, _decode_token
        token = create_access_token(user_id=99, role="USER")
        payload = _decode_token(token)
        assert payload["sub"] == "99"
        assert isinstance(payload["sub"], str)


# ──────────────────────────────────────────────────────────────────
# Rate Limiting 测试
# ──────────────────────────────────────────────────────────────────

class TestRateLimiting:
    def test_extract_user_key_no_auth(self):
        """无 Authorization header 时用 IP 作为 key"""
        from app.core.rate_limit import _extract_user_key
        from unittest.mock import MagicMock
        req = MagicMock()
        req.headers = {}
        req.client.host = "127.0.0.1"
        key = _extract_user_key(req)
        assert key.startswith("rl:ip:")

    def test_extract_user_key_with_valid_token(self):
        """有效 Bearer token 时用 JWT sub 作为 key"""
        from app.core.auth import create_access_token
        from app.core.rate_limit import _extract_user_key
        from unittest.mock import MagicMock
        token = create_access_token(user_id="42")
        req = MagicMock()
        req.headers = {"Authorization": f"Bearer {token}"}
        req.client.host = "127.0.0.1"
        key = _extract_user_key(req)
        assert key == "rl:user:42"

    def test_extract_user_key_invalid_token_falls_back_to_ip(self):
        """无效 token 降级用 IP"""
        from app.core.rate_limit import _extract_user_key
        from unittest.mock import MagicMock
        req = MagicMock()
        req.headers = {"Authorization": "Bearer INVALID_TOKEN"}
        req.client.host = "10.0.0.1"
        key = _extract_user_key(req)
        assert key.startswith("rl:ip:")

    def test_rate_limit_skips_non_target_paths(self):
        """不在限流路径上的请求不受限制"""
        import asyncio
        from unittest.mock import AsyncMock, MagicMock
        from app.core.rate_limit import RateLimitMiddleware, _RATE_LIMITED_PREFIXES

        assert "/health" not in str(_RATE_LIMITED_PREFIXES)

        middleware = RateLimitMiddleware(app=MagicMock())
        req = MagicMock()
        req.url.path = "/health"

        next_called = False

        async def mock_next(r):
            nonlocal next_called
            next_called = True
            return MagicMock()

        asyncio.get_event_loop().run_until_complete(
            middleware.dispatch(req, mock_next)
        )
        assert next_called

    def test_rate_limit_skips_in_testing_mode(self):
        """TESTING=1 时跳过所有限流"""
        import asyncio
        from unittest.mock import MagicMock
        from app.core.rate_limit import RateLimitMiddleware

        middleware = RateLimitMiddleware(app=MagicMock())
        req = MagicMock()
        req.url.path = "/api/agent/chat"

        next_called = False

        async def mock_next(r):
            nonlocal next_called
            next_called = True
            return MagicMock()

        asyncio.get_event_loop().run_until_complete(
            middleware.dispatch(req, mock_next)
        )
        assert next_called
