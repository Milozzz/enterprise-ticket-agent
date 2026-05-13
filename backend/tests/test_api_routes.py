"""
测试：FastAPI 路由 happy path

使用 httpx.AsyncClient + ASGITransport，不启动真实服务器。
数据库用 SQLite 内存模式（:memory:），不污染开发数据库。
"""

import os
import pytest
import pytest_asyncio
import httpx
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

# 测试前设置内存数据库，避免污染 ticket.db
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("ENVIRONMENT", "development")
os.environ.setdefault("GOOGLE_API_KEY", "test-key")

from app.main import app
from app.db.database import Base


@pytest_asyncio.fixture(scope="module")
async def client():
    """创建测试用 ASGI 客户端，使用内存 SQLite"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as c:
        yield c


class TestHealthEndpoint:
    async def test_health_returns_ok(self, client):
        response = await client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data.get("status") == "ok"


class TestDebugEndpoint:
    async def test_debug_blocked_in_production(self, client):
        """非 development 环境下 /debug 应返回 403"""
        with pytest.MonkeyPatch().context() as mp:
            # 临时覆盖 environment 为 production
            mp.setenv("ENVIRONMENT", "production")
            response = await client.get("/api/agent/debug/test-thread-123")
        assert response.status_code == 403

    async def test_debug_accessible_in_development(self, client):
        """/debug 在 development 环境应返回 200 或有效 JSON"""
        response = await client.get("/api/agent/debug/nonexistent-thread")
        # thread 不存在时返回 no_state，但 HTTP 应是 200
        assert response.status_code == 200
        assert response.json()["status"] == "no_state"


class TestResumeEndpoint:
    async def test_resume_rejects_non_manager(self, client):
        """非 MANAGER 角色调用 /resume 应返回 403"""
        response = await client.post("/api/agent/resume", json={
            "thread_id": "test-thread",
            "action": "approve",
            "reviewer_role": "USER",
            "reviewer_id": "user_001",
            "comment": "",
        })
        assert response.status_code == 403

    async def test_resume_accepts_manager_role(self, client):
        """MANAGER 角色调用 /resume 应返回 200（即使 thread 不存在也应流式响应）"""
        response = await client.post("/api/agent/resume", json={
            "thread_id": "nonexistent-thread-xyz",
            "action": "approve",
            "reviewer_role": "MANAGER",
            "reviewer_id": "manager_001",
            "comment": "approved",
        })
        assert response.status_code == 200
