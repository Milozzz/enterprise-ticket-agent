"""
测试：意图分类规则引擎（_rule_classify）+ LLM 分类降级

规则引擎是 LLM 降级时的保底路径，逻辑需要稳定正确。
这些测试不依赖 API Key，可在 CI 环境中直接运行。
"""

import pytest
from unittest.mock import patch, MagicMock
from app.agent.nodes.classifier import _rule_classify, classify_intent_node
from langchain_core.messages import HumanMessage


class TestRuleClassifyIntent:
    def test_refund_intent(self):
        result = _rule_classify("我要申请退款，订单号 123456")
        assert result["intent"] == "refund"

    def test_policy_intent_takes_priority_over_refund(self):
        # "退款政策" 同时含退款关键词和政策关键词，应识别为 query_policy
        result = _rule_classify("七天无理由退款怎么算？运费谁出？")
        assert result["intent"] == "query_policy"

    def test_logistics_intent(self):
        result = _rule_classify("我的快递到哪里了？")
        assert result["intent"] == "track_logistics"

    def test_query_order_intent(self):
        result = _rule_classify("查询一下我订单 789012 的最新状态")
        assert result["intent"] == "query_order"

    def test_other_intent(self):
        result = _rule_classify("你好")
        assert result["intent"] == "other"

    def test_policy_keywords_shipping_cost(self):
        result = _rule_classify("退货运费谁出？")
        assert result["intent"] == "query_policy"

    def test_policy_keywords_days(self):
        result = _rule_classify("申请退款需要几天到账？")
        assert result["intent"] == "query_policy"


class TestRuleClassifyOrderId:
    def test_extract_order_id_with_prefix(self):
        result = _rule_classify("订单号 789012 申请退款")
        assert result["order_id"] == "789012"

    def test_extract_order_id_bare_number(self):
        result = _rule_classify("退款 123456")
        assert result["order_id"] == "123456"

    def test_no_order_id(self):
        result = _rule_classify("我想了解退款政策")
        assert result["order_id"] == ""

    def test_order_id_with_hash(self):
        result = _rule_classify("#999888 申请退款")
        assert result["order_id"] == "999888"

    def test_order_id_min_4_digits(self):
        # 少于 4 位数字不应被识别为订单号
        result = _rule_classify("退款 123")
        assert result["order_id"] == ""

    def test_order_keyword_with_colon(self):
        result = _rule_classify("订单号：456789 退款")
        assert result["order_id"] == "456789"


class TestRuleClassifyReason:
    def test_damaged_reason(self):
        result = _rule_classify("收到商品有破损，申请退款")
        assert result["reason"] == "damaged"

    def test_wrong_item_reason(self):
        result = _rule_classify("发错货了，不符合我的订单")
        assert result["reason"] == "wrong_item"

    def test_not_received_reason(self):
        result = _rule_classify("商品未收到，申请退款")
        assert result["reason"] == "not_received"

    def test_quality_issue_reason(self):
        result = _rule_classify("商品质量太差了，要退款")
        assert result["reason"] == "quality_issue"

    def test_default_reason_is_other(self):
        result = _rule_classify("订单 123456 申请退款")
        assert result["reason"] == "other"

    def test_method_is_rules(self):
        result = _rule_classify("退款 999999")
        assert result["_method"] == "rules"


class TestLLMClassifyFallback:
    """测试 LLM 失败时自动降级到规则引擎"""

    @pytest.mark.asyncio
    async def test_llm_timeout_falls_back_to_rules(self):
        """LLM 超时时应降级到规则引擎，不抛出异常"""
        import asyncio

        state = {
            "messages": [HumanMessage(content="订单 789012 申请退款，商品破损")],
            "user_role": "USER",
            "thread_id": "test-thread-001",
            "trace_id": "trace-001",
            "ui_events": [],
        }

        with patch("app.agent.nodes.classifier._llm_classify",
                   side_effect=asyncio.TimeoutError("模拟超时")):
            result = await classify_intent_node(state)

        assert result["intent"] == "refund"
        assert result["order_id"] == "789012"
        assert result["refund_reason"] == "damaged"

    @pytest.mark.asyncio
    async def test_llm_api_error_falls_back_to_rules(self):
        """LLM API 错误时应降级，返回规则引擎结果"""
        state = {
            "messages": [HumanMessage(content="七天无理由退款怎么算？")],
            "user_role": "USER",
            "thread_id": "test-thread-002",
            "trace_id": "trace-002",
            "ui_events": [],
        }

        with patch("app.agent.nodes.classifier._llm_classify",
                   side_effect=Exception("API key invalid")):
            result = await classify_intent_node(state)

        assert result["intent"] == "query_policy"

    @pytest.mark.asyncio
    async def test_llm_invalid_json_falls_back_to_rules(self):
        """LLM 返回非 JSON 时应降级"""
        state = {
            "messages": [HumanMessage(content="查询订单 111222 状态")],
            "user_role": "USER",
            "thread_id": "test-thread-003",
            "trace_id": "trace-003",
            "ui_events": [],
        }

        with patch("app.agent.nodes.classifier._llm_classify",
                   side_effect=ValueError("LLM 未返回有效 JSON")):
            result = await classify_intent_node(state)

        assert result["intent"] == "query_order"
        assert result["order_id"] == "111222"

    @pytest.mark.asyncio
    async def test_empty_message_returns_error(self):
        """空消息应返回错误状态"""
        state = {
            "messages": [],
            "user_role": "USER",
            "thread_id": "test-thread-004",
            "trace_id": "trace-004",
            "ui_events": [],
        }

        result = await classify_intent_node(state)
        assert "error_message" in result

    @pytest.mark.asyncio
    async def test_ui_events_always_emitted(self):
        """无论成功还是降级，都应发出 thinking_stream UI 事件"""
        state = {
            "messages": [HumanMessage(content="退款 789012")],
            "user_role": "USER",
            "thread_id": "test-thread-005",
            "trace_id": "trace-005",
            "ui_events": [],
        }

        with patch("app.agent.nodes.classifier._llm_classify",
                   side_effect=Exception("模拟错误")):
            result = await classify_intent_node(state)

        assert len(result.get("ui_events", [])) > 0
        assert result["ui_events"][0]["type"] == "thinking_stream"
