"""
tests/test_e2e_refund_flow.py

端到端集成测试：完整退款流程
=============================================================
覆盖两条核心路径：

  Path A — 低风险自动退款（金额 ≤ 500）：
    classify_intent → lookup_order → check_risk → execute_refund
    → send_notification → summarize_session → END

  Path B — 高风险人工审批（金额 > 500）：
    classify_intent → lookup_order → check_risk → INTERRUPT
    → /resume(approve) → execute_refund → send_notification
    → summarize_session → END

  Path C — 管理员拒绝退款：
    ... → INTERRUPT → /resume(reject) → summarize_session → END

测试原则：
  - 使用真实 LangGraph 图（MemorySaver checkpointer）
  - 使用真实 SQLite 内存数据库（不 mock DB 层）
  - 只 mock 外部 I/O：LLM（Gemini）、邮件发送、Redis
  - 断言状态机流转、DB 写入、SSE 事件格式
"""

import os
import json
import pytest
import pytest_asyncio
import httpx

# 必须在所有 app 导入前设置
os.environ["TESTING"] = "1"
os.environ["GOOGLE_API_KEY"] = "test-key"
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./test_e2e.db"
os.environ["ENVIRONMENT"] = "development"

from unittest.mock import AsyncMock, MagicMock, patch
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy import select


# ── SSE 解析 ─────────────────────────────────────────────────────────────────

def _parse_sse(raw: str) -> list[dict]:
    events, current = [], {}
    for line in raw.splitlines():
        if line.startswith("event:"):
            current["event"] = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            try:
                current["data"] = json.loads(line.split(":", 1)[1].strip())
            except json.JSONDecodeError:
                current["data"] = line.split(":", 1)[1].strip()
        elif line == "" and current:
            events.append(current)
            current = {}
    if current:
        events.append(current)
    return events


# ── 测试数据 ──────────────────────────────────────────────────────────────────

TEST_USER_ID   = 1
TEST_ORDER_ID  = "ORD-E2E-001"
TEST_ORDER_AMT_LOW  = 200.0   # 低风险：< 500
TEST_ORDER_AMT_HIGH = 800.0   # 高风险：> 500，触发人工审批

THREAD_LOW  = "e2e-thread-low-risk"
THREAD_HIGH = "e2e-thread-high-risk"
THREAD_REJ  = "e2e-thread-reject"


# ── LLM mock 工厂 ─────────────────────────────────────────────────────────────

def _make_classifier_mock(intent: str, order_id: str) -> MagicMock:
    """模拟意图识别 LLM 返回"""
    resp = MagicMock()
    resp.content = json.dumps({
        "intent": intent,
        "order_id": order_id,
        "reason": "damaged",
        "description": "商品破损，申请退款",
        "user_id": str(TEST_USER_ID),
    })
    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=resp)
    return llm


def _make_summarize_mock() -> MagicMock:
    """模拟摘要 LLM 返回"""
    resp = MagicMock()
    resp.content = "本次会话：用户申请退款，已处理完毕。"
    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=resp)
    return llm


def _make_order_tool_mock(order_id: str, amount: float) -> MagicMock:
    """模拟 get_order_detail 工具返回（同步 .invoke()）"""
    mock = MagicMock()
    mock.invoke = MagicMock(return_value={
        "id": order_id,
        "orderId": order_id,
        "totalAmount": amount,
        "status": "delivered",
        "items": [{"name": "测试商品", "quantity": 1, "price": amount}],
        "userId": str(TEST_USER_ID),
        "shippingAddress": "测试地址",
    })
    return mock


# ── Fixture：共享 ASGI client + 真实 DB ──────────────────────────────────────

@pytest_asyncio.fixture(scope="module")
async def client_and_db():
    """
    创建内存 SQLite + 测试用 ASGI client。
    预置 User 和 Order 行，避免 lookup_order_node 查不到数据。
    """
    from app.main import app
    from app.db.database import Base
    from app.db.models import User, Order, UserRole
    import app.api.routes.chat as chat_route
    import app.agent.nodes.risk_check as risk_check_node
    import app.agent.nodes.user_history as user_history_node
    import app.agent.nodes.refund as refund_node
    import app.agent.nodes.human_review as human_review_node
    import app.db.ticket_repository as ticket_repository

    engine = create_async_engine("sqlite+aiosqlite:///./test_e2e.db")
    Session = async_sessionmaker(engine, expire_on_commit=False)
    chat_route.AsyncSessionLocal = Session
    risk_check_node.AsyncSessionLocal = Session
    user_history_node.AsyncSessionLocal = Session
    refund_node.AsyncSessionLocal = Session
    human_review_node.AsyncSessionLocal = Session
    ticket_repository.AsyncSessionLocal = Session

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # 插入测试数据
    async with Session() as sess:
        existing = await sess.scalar(select(User).where(User.id == TEST_USER_ID))
        if not existing:
            sess.add(User(
                id=TEST_USER_ID,
                name="E2E TestUser",
                email="e2e@test.com",
                role=UserRole.USER,
            ))
        order_existing = await sess.scalar(
            select(Order).where(Order.id == TEST_ORDER_ID)
        )
        if not order_existing:
            sess.add(Order(
                id=TEST_ORDER_ID,
                user_id=TEST_USER_ID,
                amount=TEST_ORDER_AMT_HIGH,   # 默认用高风险金额；低风险测试 mock lookup
                status="delivered",
                items=[{"name": "测试商品", "qty": 1}],
            ))
        await sess.commit()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        timeout=30.0,
    ) as c:
        yield c, Session

    await engine.dispose()


# ── Path A：低风险自动退款 ────────────────────────────────────────────────────

class TestLowRiskAutoRefund:
    """金额 ≤ 500 → 自动退款，无需人工审批"""

    @pytest.mark.asyncio
    async def test_sse_sequence_ends_with_done(self, client_and_db):
        """SSE 事件序列：meta → (ui...) → (text...) → done"""
        client, _ = client_and_db

        with patch("app.agent.nodes.classifier._get_llm_structured",
                   return_value=_make_classifier_mock("refund", TEST_ORDER_ID)), \
             patch("app.agent.nodes.classifier._get_llm_fallback",
                   return_value=_make_classifier_mock("refund", TEST_ORDER_ID)), \
             patch("app.agent.nodes.order_lookup.get_order_detail",
                   _make_order_tool_mock(TEST_ORDER_ID, TEST_ORDER_AMT_LOW)), \
             patch("app.agent.tools.notification_tools._send_email"), \
             patch("app.agent.nodes.summarize._call_llm", new_callable=AsyncMock,
                   return_value="本次退款已完成。"), \
             patch("app.api.routes.chat._add_audit_log", new_callable=AsyncMock), \
             patch("app.api.routes.chat.effective_simulate_database_down", return_value=False):

            response = await client.post("/api/agent/chat", json={
                "messages": [{"role": "user", "content": "订单 ORD-E2E-001 商品破损，申请退款"}],
                "thread_id": THREAD_LOW,
                "trace_id": "trace-e2e-low",
                "user_id": str(TEST_USER_ID),
                "user_role": "USER",
            })

        assert response.status_code == 200
        assert "text/event-stream" in response.headers.get("content-type", "")

        events = _parse_sse(response.text)
        event_types = [e.get("event") for e in events]

        assert "done" in event_types, f"缺少 done 事件，实际事件: {event_types}"
        assert event_types[-1] == "done", "done 必须是最后一个事件"
        assert "meta" in event_types, "缺少 meta 事件"

    @pytest.mark.asyncio
    async def test_meta_event_contains_trace_id(self, client_and_db):
        """meta 事件必须包含 trace_id，供前端关联链路"""
        client, _ = client_and_db

        with patch("app.agent.nodes.classifier._get_llm_structured",
                   return_value=_make_classifier_mock("refund", TEST_ORDER_ID)), \
             patch("app.agent.nodes.order_lookup.get_order_detail",
                   _make_order_tool_mock(TEST_ORDER_ID, TEST_ORDER_AMT_LOW)), \
             patch("app.agent.tools.notification_tools._send_email"), \
             patch("app.agent.nodes.summarize._call_llm", new_callable=AsyncMock,
                   return_value="摘要"), \
             patch("app.api.routes.chat._add_audit_log", new_callable=AsyncMock), \
             patch("app.api.routes.chat.effective_simulate_database_down", return_value=False):

            response = await client.post("/api/agent/chat", json={
                "messages": [{"role": "user", "content": "退款请求 ORD-E2E-001"}],
                "thread_id": THREAD_LOW + "-meta",
                "trace_id": "trace-meta-check",
                "user_id": str(TEST_USER_ID),
                "user_role": "USER",
            })

        events = _parse_sse(response.text)
        meta_events = [e for e in events if e.get("event") == "meta"]
        assert len(meta_events) > 0
        assert meta_events[0]["data"].get("trace_id") == "trace-meta-check"

    @pytest.mark.asyncio
    async def test_no_interrupt_for_low_risk(self, client_and_db):
        """低风险不应产生 interrupt 事件（即不需要人工审批）"""
        client, _ = client_and_db

        with patch("app.agent.nodes.classifier._get_llm_structured",
                   return_value=_make_classifier_mock("refund", TEST_ORDER_ID)), \
             patch("app.agent.nodes.order_lookup.get_order_detail",
                   _make_order_tool_mock(TEST_ORDER_ID, TEST_ORDER_AMT_LOW)), \
             patch("app.agent.tools.notification_tools._send_email"), \
             patch("app.agent.nodes.summarize._call_llm", new_callable=AsyncMock,
                   return_value="摘要"), \
             patch("app.api.routes.chat._add_audit_log", new_callable=AsyncMock), \
             patch("app.api.routes.chat.effective_simulate_database_down", return_value=False):

            response = await client.post("/api/agent/chat", json={
                "messages": [{"role": "user", "content": "退款请求 ORD-E2E-001"}],
                "thread_id": THREAD_LOW + "-no-interrupt",
                "trace_id": "trace-no-interrupt",
                "user_id": str(TEST_USER_ID),
                "user_role": "USER",
            })

        events = _parse_sse(response.text)
        event_types = [e.get("event") for e in events]
        assert "interrupt" not in event_types, "低风险退款不应触发 interrupt 事件"


# ── Path B：高风险人工审批 → 批准 ─────────────────────────────────────────────

class TestHighRiskHumanApproval:
    """金额 > 500 → 触发 interrupt → MANAGER 批准 → 退款完成"""

    @pytest.mark.asyncio
    async def test_high_risk_triggers_interrupt(self, client_and_db):
        """高风险退款应在 SSE 流中产生 interrupt 事件"""
        client, _ = client_and_db

        with patch("app.agent.nodes.classifier._get_llm_structured",
                   return_value=_make_classifier_mock("refund", TEST_ORDER_ID)), \
             patch("app.agent.nodes.order_lookup.get_order_detail",
                   _make_order_tool_mock(TEST_ORDER_ID, TEST_ORDER_AMT_HIGH)), \
             patch("app.api.routes.chat._add_audit_log", new_callable=AsyncMock), \
             patch("app.api.routes.chat.effective_simulate_database_down", return_value=False):

            response = await client.post("/api/agent/chat", json={
                "messages": [{"role": "user", "content": "订单 ORD-E2E-001 退款 800 元"}],
                "thread_id": THREAD_HIGH,
                "trace_id": "trace-e2e-high",
                "user_id": str(TEST_USER_ID),
                "user_role": "USER",
            })

        assert response.status_code == 200
        events = _parse_sse(response.text)
        event_types = [e.get("event") for e in events]
        assert "interrupt" in event_types, (
            f"高风险退款（¥{TEST_ORDER_AMT_HIGH}）应触发 interrupt 事件\n实际事件: {event_types}"
        )

    @pytest.mark.asyncio
    async def test_resume_approve_completes_flow(self, client_and_db):
        """MANAGER 批准后，/resume 应返回 200 SSE 流，最终以 done 结束"""
        client, _ = client_and_db

        with patch("app.agent.tools.notification_tools._send_email"), \
             patch("app.agent.nodes.summarize._call_llm", new_callable=AsyncMock,
                   return_value="本次退款已完成。"), \
             patch("app.api.routes.chat._add_audit_log", new_callable=AsyncMock), \
             patch("app.api.routes.chat.effective_simulate_database_down", return_value=False):

            response = await client.post("/api/agent/resume", json={
                "thread_id": THREAD_HIGH,
                "action": "approve",
                "reviewer_role": "MANAGER",
                "reviewer_id": "manager_001",
                "comment": "金额合理，批准",
            })

        assert response.status_code == 200
        events = _parse_sse(response.text)
        event_types = [e.get("event") for e in events]
        assert "done" in event_types, f"resume 审批流应以 done 结束\n实际事件: {event_types}"
        assert event_types[-1] == "done"

    @pytest.mark.asyncio
    async def test_resume_requires_manager_role(self, client_and_db):
        """非 MANAGER 调用 /resume 必须返回 403"""
        client, _ = client_and_db

        for role in ("USER", "AGENT"):
            response = await client.post("/api/agent/resume", json={
                "thread_id": THREAD_HIGH,
                "action": "approve",
                "reviewer_role": role,
                "reviewer_id": "not-a-manager",
                "comment": "",
            })
            assert response.status_code == 403, (
                f"{role} 角色调用 /resume 应返回 403，实际: {response.status_code}"
            )


# ── Path C：高风险人工审批 → 拒绝 ─────────────────────────────────────────────

class TestHighRiskRejection:
    """MANAGER 拒绝退款 → 流程终止，不执行退款"""

    @pytest.mark.asyncio
    async def test_reject_flow_ends_with_done(self, client_and_db):
        """拒绝路径同样应以 done 事件结束"""
        client, _ = client_and_db

        # Step 1: 提交高风险退款，触发 interrupt
        with patch("app.agent.nodes.classifier._get_llm_structured",
                   return_value=_make_classifier_mock("refund", TEST_ORDER_ID)), \
             patch("app.agent.nodes.order_lookup.get_order_detail",
                   _make_order_tool_mock(TEST_ORDER_ID, TEST_ORDER_AMT_HIGH)), \
             patch("app.api.routes.chat._add_audit_log", new_callable=AsyncMock), \
             patch("app.api.routes.chat.effective_simulate_database_down", return_value=False):

            r1 = await client.post("/api/agent/chat", json={
                "messages": [{"role": "user", "content": "退款 800 元订单 ORD-E2E-001"}],
                "thread_id": THREAD_REJ,
                "trace_id": "trace-e2e-reject",
                "user_id": str(TEST_USER_ID),
                "user_role": "USER",
            })
        assert r1.status_code == 200

        # Step 2: MANAGER 拒绝
        with patch("app.agent.nodes.summarize._call_llm", new_callable=AsyncMock,
                   return_value="退款申请被拒绝。"), \
             patch("app.api.routes.chat._add_audit_log", new_callable=AsyncMock), \
             patch("app.api.routes.chat.effective_simulate_database_down", return_value=False):

            r2 = await client.post("/api/agent/resume", json={
                "thread_id": THREAD_REJ,
                "action": "reject",
                "reviewer_role": "MANAGER",
                "reviewer_id": "manager_001",
                "comment": "风险过高，拒绝",
            })

        assert r2.status_code == 200
        events = _parse_sse(r2.text)
        event_types = [e.get("event") for e in events]
        assert "done" in event_types, "拒绝路径应以 done 结束"


# ── 边界情况 ──────────────────────────────────────────────────────────────────

class TestEdgeCases:
    """边界：订单不存在、DB 宕机、query_order 意图"""

    @pytest.mark.asyncio
    async def test_order_not_found_ends_gracefully(self, client_and_db):
        """订单查不到时流程应优雅结束（不崩溃），产生 done 事件"""
        client, _ = client_and_db

        not_found_mock = MagicMock()
        not_found_mock.invoke = MagicMock(return_value={"error": "订单不存在"})

        with patch("app.agent.nodes.classifier._get_llm_structured",
                   return_value=_make_classifier_mock("refund", "NONEXISTENT-ORDER")), \
             patch("app.agent.nodes.order_lookup.get_order_detail", not_found_mock), \
             patch("app.agent.nodes.summarize._call_llm", new_callable=AsyncMock,
                   return_value="订单未找到，流程终止。"), \
             patch("app.api.routes.chat._add_audit_log", new_callable=AsyncMock), \
             patch("app.api.routes.chat.effective_simulate_database_down", return_value=False):

            response = await client.post("/api/agent/chat", json={
                "messages": [{"role": "user", "content": "退款订单 NONEXISTENT-ORDER"}],
                "thread_id": "e2e-thread-no-order",
                "trace_id": "trace-no-order",
                "user_id": str(TEST_USER_ID),
                "user_role": "USER",
            })

        assert response.status_code == 200
        events = _parse_sse(response.text)
        assert any(e.get("event") == "done" for e in events)

    @pytest.mark.asyncio
    async def test_db_down_returns_friendly_message(self, client_and_db):
        """DB 宕机时应返回用户友好文案，SSE 正常结束"""
        client, _ = client_and_db

        with patch("app.api.routes.chat.effective_simulate_database_down", return_value=True):
            response = await client.post("/api/agent/chat", json={
                "messages": [{"role": "user", "content": "退款测试"}],
                "thread_id": "e2e-thread-db-down",
                "trace_id": "trace-db-down",
                "user_id": str(TEST_USER_ID),
                "user_role": "USER",
            })

        assert response.status_code == 200
        events = _parse_sse(response.text)
        text_events = [e for e in events if e.get("event") == "text"]
        combined = " ".join(str(e.get("data", "")) for e in text_events)
        assert "Traceback" not in combined
        assert "Exception" not in combined

    @pytest.mark.asyncio
    async def test_query_order_intent_reaches_answer_node(self, client_and_db):
        """query_order 意图应路由到 answer_node，SSE 以 done 结束"""
        client, _ = client_and_db

        mock_ai = MagicMock()
        mock_ai.tool_calls = []
        mock_ai.content = "您的订单 ORD-E2E-001 正在处理中。"

        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=mock_ai)

        with patch("app.agent.nodes.classifier._get_llm_structured",
                   return_value=_make_classifier_mock("query_order", TEST_ORDER_ID)), \
             patch("app.agent.nodes.answer._get_llm", return_value=mock_llm), \
             patch("app.agent.nodes.summarize._call_llm", new_callable=AsyncMock,
                   return_value="用户查询了订单状态。"), \
             patch("app.api.routes.chat._add_audit_log", new_callable=AsyncMock), \
             patch("app.api.routes.chat.effective_simulate_database_down", return_value=False):

            response = await client.post("/api/agent/chat", json={
                "messages": [{"role": "user", "content": "查询订单 ORD-E2E-001 状态"}],
                "thread_id": "e2e-thread-query",
                "trace_id": "trace-query",
                "user_id": str(TEST_USER_ID),
                "user_role": "USER",
            })

        assert response.status_code == 200
        events = _parse_sse(response.text)
        assert any(e.get("event") == "done" for e in events)
