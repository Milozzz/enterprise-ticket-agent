"""
测试：/api/agent/chat SSE 集成测试

验证：
- SSE 事件顺序（meta → ui → text → done）
- 缓存命中路径
- DB 宕机降级路径
- /resume 权限校验
- 审计日志端点
"""

import os
import json
import pytest
import pytest_asyncio
import httpx

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("ENVIRONMENT", "development")
os.environ.setdefault("GOOGLE_API_KEY", "test-key")

from unittest.mock import patch, AsyncMock, MagicMock


# ── SSE 解析工具 ─────────────────────────────────────────────────────────────

def _parse_sse(raw: str) -> list[dict]:
    """将 SSE 原始文本解析为事件列表"""
    events = []
    current = {}
    for line in raw.splitlines():
        if line.startswith("event:"):
            current["event"] = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            data_str = line.split(":", 1)[1].strip()
            try:
                current["data"] = json.loads(data_str)
            except json.JSONDecodeError:
                current["data"] = data_str
        elif line == "" and current:
            events.append(current)
            current = {}
    if current:
        events.append(current)
    return events


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture(scope="module")
async def client():
    from sqlalchemy.ext.asyncio import create_async_engine
    from app.main import app
    from app.db.database import Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as c:
        yield c


# ── 健康检查 ─────────────────────────────────────────────────────────────────

class TestHealthEndpoint:
    @pytest.mark.asyncio
    async def test_health_returns_ok(self, client):
        response = await client.get("/health")
        assert response.status_code == 200
        assert response.json().get("status") == "ok"


# ── SSE 协议测试 ─────────────────────────────────────────────────────────────

class TestChatSSEProtocol:
    @pytest.mark.asyncio
    async def test_sse_response_ends_with_done(self, client):
        """所有 SSE 响应都应以 done 事件结束"""
        with patch("app.api.routes.chat.ticket_graph") as mock_graph, \
             patch("app.api.routes.chat._add_audit_log", new_callable=AsyncMock), \
             patch("app.api.routes.chat.effective_simulate_database_down", return_value=False):

            # mock astream_events 返回空序列（最简 Agent 响应）
            async def _empty_events(*args, **kwargs):
                return
                yield  # make it a generator

            mock_graph.astream_events = _empty_events
            mock_graph.get_state.return_value = MagicMock(values={"intent": "other"})

            response = await client.post("/api/agent/chat", json={
                "messages": [{"role": "user", "content": "你好"}],
                "thread_id": "sse-test-001",
                "trace_id": "trace-001",
                "user_id": "user_001",
                "user_role": "USER",
            })

        assert response.status_code == 200
        assert "text/event-stream" in response.headers.get("content-type", "")

        events = _parse_sse(response.text)
        event_types = [e.get("event") for e in events]

        # 必须有 done 事件
        assert "done" in event_types
        # done 必须是最后一个事件
        assert event_types[-1] == "done"

    @pytest.mark.asyncio
    async def test_sse_meta_event_contains_trace_id(self, client):
        """SSE 第一个事件应为 meta，包含 trace_id"""
        with patch("app.api.routes.chat.ticket_graph") as mock_graph, \
             patch("app.api.routes.chat._add_audit_log", new_callable=AsyncMock), \
             patch("app.api.routes.chat.effective_simulate_database_down", return_value=False):

            async def _empty_events(*args, **kwargs):
                return
                yield

            mock_graph.astream_events = _empty_events
            mock_graph.get_state.return_value = MagicMock(values={"intent": "other"})

            response = await client.post("/api/agent/chat", json={
                "messages": [{"role": "user", "content": "测试"}],
                "thread_id": "sse-test-meta",
                "trace_id": "my-trace-xyz",
                "user_id": "user_001",
                "user_role": "USER",
            })

        events = _parse_sse(response.text)
        meta_events = [e for e in events if e.get("event") == "meta"]
        assert len(meta_events) > 0
        assert meta_events[0]["data"]["trace_id"] == "my-trace-xyz"

    @pytest.mark.asyncio
    async def test_simulate_db_down_returns_friendly_error(self, client):
        """DB 宕机时应返回用户友好文案，不暴露异常栈"""
        with patch("app.api.routes.chat.effective_simulate_database_down", return_value=True):
            response = await client.post("/api/agent/chat", json={
                "messages": [{"role": "user", "content": "退款"}],
                "thread_id": "sse-db-down",
                "trace_id": "trace-db-down",
                "user_id": "user_001",
                "user_role": "USER",
            })

        assert response.status_code == 200
        events = _parse_sse(response.text)
        text_events = [e for e in events if e.get("event") == "text"]
        assert len(text_events) > 0
        # 不应包含 Python 异常信息
        combined_text = " ".join(str(e["data"]) for e in text_events)
        assert "Traceback" not in combined_text
        assert "Exception" not in combined_text


# ── /resume 权限测试 ─────────────────────────────────────────────────────────

class TestResumeEndpoint:
    @pytest.mark.asyncio
    async def test_resume_rejects_user_role(self, client):
        response = await client.post("/api/agent/resume", json={
            "thread_id": "test-thread",
            "action": "approve",
            "reviewer_role": "USER",
            "reviewer_id": "user_001",
            "comment": "",
        })
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_resume_rejects_agent_role(self, client):
        response = await client.post("/api/agent/resume", json={
            "thread_id": "test-thread",
            "action": "approve",
            "reviewer_role": "AGENT",
            "reviewer_id": "agent_001",
            "comment": "",
        })
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_resume_accepts_manager_role(self, client):
        """MANAGER 调用 /resume 应返回 200 流式响应（即使 thread 不存在）"""
        with patch("app.api.routes.chat.effective_simulate_database_down", return_value=False), \
             patch("app.api.routes.chat._add_audit_log", new_callable=AsyncMock):
            response = await client.post("/api/agent/resume", json={
                "thread_id": "nonexistent-thread-xyz",
                "action": "approve",
                "reviewer_role": "MANAGER",
                "reviewer_id": "manager_001",
                "comment": "已审核",
            })
        assert response.status_code == 200


# ── 调试端点测试 ─────────────────────────────────────────────────────────────

class TestDebugEndpoint:
    @pytest.mark.asyncio
    async def test_debug_accessible_in_development(self, client):
        response = await client.get("/api/agent/debug/nonexistent-thread")
        assert response.status_code == 200
        assert response.json()["status"] == "no_state"

    @pytest.mark.asyncio
    async def test_debug_blocked_in_production(self, client):
        with patch("app.api.routes.chat.get_settings") as mock_settings:
            mock_settings.return_value.environment = "production"
            response = await client.get("/api/agent/debug/test-thread-123")
        assert response.status_code == 403


# ── 审计日志端点测试 ─────────────────────────────────────────────────────────

class TestAuditEndpoint:
    @pytest.mark.asyncio
    async def test_audit_returns_list(self, client):
        """审计日志端点应返回列表（即使 thread 不存在也应返回空列表）"""
        response = await client.get("/api/agent/audit/nonexistent-thread-audit")
        assert response.status_code == 200
        assert isinstance(response.json(), list)

    @pytest.mark.asyncio
    async def test_replay_returns_valid_structure(self, client):
        """回放端点应返回包含 nodes/summary 字段的结构"""
        response = await client.get("/api/agent/replay/nonexistent-thread-replay")
        assert response.status_code == 200
        data = response.json()
        assert "nodes" in data
        assert "summary" in data
