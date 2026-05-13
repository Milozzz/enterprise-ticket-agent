"""
tests/test_answer_node.py

answer_node bind_tools ReAct 行为测试
"""

import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

os.environ["TESTING"] = "1"
os.environ["GOOGLE_API_KEY"] = "test-key"
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./test_answer.db"


def _make_state(intent="other", order_id="", messages=None):
    from langchain_core.messages import HumanMessage
    return {
        "intent": intent,
        "order_id": order_id,
        "messages": messages or [HumanMessage(content="你好")],
        "user_id": "1",
        "thread_id": "test-thread",
    }


class TestAnswerNodeFallback:
    """LLM 不可用时的降级行为"""

    @pytest.mark.asyncio
    async def test_no_llm_returns_fallback(self):
        """GOOGLE_API_KEY 无效时，_get_llm() 返回 None，应走 fallback"""
        from app.agent.nodes import answer as answer_module
        original = answer_module._llm_with_tools
        answer_module._llm_with_tools = None

        # 临时置空 api key
        import app.agent.nodes.answer as ans_mod
        original_key = ans_mod.settings.google_api_key
        ans_mod.settings = MagicMock()
        ans_mod.settings.google_api_key = ""

        from app.agent.nodes.answer import answer_node
        state = _make_state()
        result = await answer_node(state)

        assert result["is_completed"] is True
        assert "reply_text" in result
        assert len(result["reply_text"]) > 0

        answer_module._llm_with_tools = original
        ans_mod.settings = ans_mod.settings  # restore

    @pytest.mark.asyncio
    async def test_llm_timeout_returns_fallback(self):
        """LLM 超时时返回 fallback 而非抛异常"""
        import asyncio
        from langchain_core.messages import HumanMessage

        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(side_effect=asyncio.TimeoutError())

        with patch("app.agent.nodes.answer._get_llm", return_value=mock_llm):
            from app.agent.nodes.answer import answer_node
            state = _make_state()
            result = await answer_node(state)

        assert result["is_completed"] is True
        assert "reply_text" in result


class TestAnswerNodeToolCalls:
    """LLM 决定调用工具时的路由行为"""

    @pytest.mark.asyncio
    async def test_tool_call_response_returns_messages(self):
        """LLM 返回 tool_calls 时，结果 messages 包含 AIMessage，current_step 为 tool_calling"""
        from langchain_core.messages import HumanMessage, AIMessage

        # 模拟有 tool_calls 的 AIMessage
        ai_msg = MagicMock(spec=AIMessage)
        ai_msg.tool_calls = [{"name": "get_ticket_status", "args": {"order_id": "123"}, "id": "t1"}]
        ai_msg.content = ""

        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=ai_msg)

        with patch("app.agent.nodes.answer._get_llm", return_value=mock_llm):
            from app.agent.nodes.answer import answer_node
            state = _make_state(intent="query_order", order_id="123")
            result = await answer_node(state)

        assert result["current_step"] == "answer_tool_calling"
        assert "messages" in result
        assert len(result["messages"]) > 0

    @pytest.mark.asyncio
    async def test_no_tool_call_returns_reply_text(self):
        """LLM 无 tool_calls 时，直接返回 reply_text 和 answer_done"""
        from langchain_core.messages import AIMessage

        ai_msg = MagicMock(spec=AIMessage)
        ai_msg.tool_calls = []
        ai_msg.content = "您好，有什么可以帮助您的？"

        mock_llm = MagicMock()
        mock_llm.ainvoke = AsyncMock(return_value=ai_msg)

        with patch("app.agent.nodes.answer._get_llm", return_value=mock_llm):
            from app.agent.nodes.answer import answer_node
            state = _make_state()
            result = await answer_node(state)

        assert result["current_step"] == "answer_done"
        assert result["reply_text"] == "您好，有什么可以帮助您的？"
        assert result["is_completed"] is True


class TestRouteAnswer:
    """route_answer 路由函数测试"""

    def test_routes_to_tools_when_tool_calls_present(self):
        from app.agent.nodes.answer import route_answer
        from langchain_core.messages import AIMessage

        ai_msg = MagicMock(spec=AIMessage)
        ai_msg.tool_calls = [{"name": "get_ticket_status"}]

        state = {"messages": [ai_msg], "intent": "query_order"}
        assert route_answer(state) == "tools"

    def test_routes_to_summarize_when_no_tool_calls(self):
        from app.agent.nodes.answer import route_answer
        from langchain_core.messages import AIMessage

        ai_msg = MagicMock(spec=AIMessage)
        ai_msg.tool_calls = []

        state = {"messages": [ai_msg], "intent": "query_order"}
        assert route_answer(state) == "summarize"

    def test_routes_to_summarize_when_no_messages(self):
        from app.agent.nodes.answer import route_answer
        state = {"messages": [], "intent": "other"}
        result = route_answer(state)
        assert result in ("summarize", "end")


class TestShouldSummarize:
    """should_summarize 条件路由测试"""

    def test_substantive_intent_triggers_summarize(self):
        from app.agent.nodes.summarize import should_summarize
        from langchain_core.messages import HumanMessage, AIMessage
        state = {
            "intent": "query_order",
            "messages": [HumanMessage(content="查询"), AIMessage(content="结果")],
        }
        assert should_summarize(state) == "summarize"

    def test_other_intent_skips_summarize(self):
        from app.agent.nodes.summarize import should_summarize
        from langchain_core.messages import HumanMessage
        state = {
            "intent": "other",
            "messages": [HumanMessage(content="你好"), HumanMessage(content="再见")],
        }
        assert should_summarize(state) == "end"

    def test_single_message_skips_summarize(self):
        from app.agent.nodes.summarize import should_summarize
        from langchain_core.messages import HumanMessage
        state = {
            "intent": "query_order",
            "messages": [HumanMessage(content="查询")],
        }
        assert should_summarize(state) == "end"

    def test_refund_intent_triggers_summarize(self):
        from app.agent.nodes.summarize import should_summarize
        from langchain_core.messages import HumanMessage, AIMessage
        state = {
            "intent": "refund",
            "messages": [HumanMessage(content="退款"), AIMessage(content="已处理")],
        }
        assert should_summarize(state) == "summarize"
