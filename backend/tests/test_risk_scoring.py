"""
测试：风控评分逻辑（check_risk_level tool）

直接调用 tool 底层函数，验证评分规则和路由决策是否正确。
不依赖数据库或 LLM，可在 CI 中直接运行。
"""

import pytest
from unittest.mock import patch
from app.agent.tools.refund_tools import check_risk_level


def _invoke(amount: float, reason: str = "other", user_id: str = "user_001") -> dict:
    """调用 check_risk_level tool 的辅助函数"""
    return check_risk_level.invoke({
        "order_id": "TEST001",
        "amount": amount,
        "user_id": user_id,
        "reason": reason,
    })


class TestRiskScoring:
    def test_low_amount_auto_approve(self):
        """金额低于阈值（500），应自动审批"""
        result = _invoke(amount=100.0, reason="damaged")
        assert result["autoApprove"] is True
        assert result["riskScore"] < 40

    def test_amount_above_threshold_triggers_review(self):
        """金额超过 500 元阈值（+40 分），应触发人工审批"""
        result = _invoke(amount=600.0, reason="damaged")
        assert result["autoApprove"] is False
        assert result["riskScore"] >= 40

    def test_amount_above_1000_adds_extra_score(self):
        """金额超过 1000 元额外加 20 分"""
        result_600 = _invoke(amount=600.0, reason="damaged")
        result_1200 = _invoke(amount=1200.0, reason="damaged")
        assert result_1200["riskScore"] == result_600["riskScore"] + 20

    def test_risky_reason_adds_score(self):
        """高风险原因（not_received / other）加 20 分"""
        result_safe = _invoke(amount=100.0, reason="damaged")
        result_risky = _invoke(amount=100.0, reason="not_received")
        assert result_risky["riskScore"] == result_safe["riskScore"] + 20

    def test_repeat_user_adds_score(self):
        """高频退款用户（user_demo）加 10 分"""
        result_normal = _invoke(amount=100.0, user_id="user_001")
        result_repeat = _invoke(amount=100.0, user_id="user_demo")
        assert result_repeat["riskScore"] == result_normal["riskScore"] + 10

    def test_risk_score_capped_at_100(self):
        """风险分最高 100 分"""
        result = _invoke(amount=2000.0, reason="not_received", user_id="user_demo")
        assert result["riskScore"] <= 100

    def test_risk_level_classification(self):
        """风险等级分类：low / medium / high"""
        assert _invoke(amount=100.0, reason="damaged")["riskLevel"] == "low"
        # 500 < amount → +40，reason=other → +20，共 60 → high
        result = _invoke(amount=600.0, reason="other")
        assert result["riskLevel"] == "high"

    def test_custom_threshold_via_settings(self):
        """阈值从 settings 读取，可配置"""
        with patch("app.agent.tools.refund_tools.settings") as mock_settings:
            mock_settings.risk_threshold_amount = 200.0
            result = _invoke(amount=300.0, reason="damaged")
            assert result["autoApprove"] is False


class TestRiskBoundaryAmounts:
    """精确边界值测试：499 / 500 / 501 是最常被问到的面试场景"""

    def test_amount_499_auto_approve(self):
        """¥499 < 阈值 ¥500，应自动审批"""
        result = _invoke(amount=499.0, reason="damaged")
        assert result["autoApprove"] is True
        assert result["riskScore"] < 40

    def test_amount_500_exact_not_over(self):
        """¥500 == 阈值，check_risk_level 用严格大于（>），刚好等于不触发"""
        result = _invoke(amount=500.0, reason="damaged")
        # amount > 500 才加分，所以 500.0 不触发阈值
        assert result["autoApprove"] is True

    def test_amount_501_triggers_review(self):
        """¥501 > 阈值，应触发人工审批"""
        result = _invoke(amount=501.0, reason="damaged")
        assert result["autoApprove"] is False
        assert result["riskScore"] >= 40

    def test_amount_1000_exact_not_extra(self):
        """¥1000 == 第二个阈值，amount > 1000 才加分，刚好不触发"""
        result_1000 = _invoke(amount=1000.0, reason="damaged")
        result_600 = _invoke(amount=600.0, reason="damaged")
        # 两者都只触发 >500 的 +40 分，分数应相同
        assert result_1000["riskScore"] == result_600["riskScore"]

    def test_amount_1001_triggers_extra_score(self):
        """¥1001 > ¥1000，额外加 20 分"""
        result_600 = _invoke(amount=600.0, reason="damaged")
        result_1001 = _invoke(amount=1001.0, reason="damaged")
        assert result_1001["riskScore"] == result_600["riskScore"] + 20

    def test_all_risk_factors_combined(self):
        """最高风险组合：大额 + 高风险原因 + 高频用户 → 应为 high"""
        result = _invoke(amount=1500.0, reason="not_received", user_id="user_demo")
        assert result["riskLevel"] == "high"
        assert result["autoApprove"] is False
        # 1500 > 500 (+40) + 1500 > 1000 (+20) + not_received (+20) + user_demo (+10) = 90
        assert result["riskScore"] == 90

    def test_reasons_list_not_empty_when_risky(self):
        """高风险时 reasons 列表不应为空"""
        result = _invoke(amount=600.0, reason="not_received", user_id="user_demo")
        assert len(result["reasons"]) > 0

    def test_reasons_list_has_default_when_clean(self):
        """低风险时 reasons 应有默认的「无明显风险」提示"""
        result = _invoke(amount=100.0, reason="damaged", user_id="user_001")
        assert len(result["reasons"]) > 0
        assert result["reasons"][0] == "未发现明显风险因素"

    def test_threshold_field_returned(self):
        """返回值中应包含 threshold 字段，便于前端展示"""
        result = _invoke(amount=100.0)
        assert "threshold" in result
        assert result["threshold"] == 500.0

    def test_recommendation_field_returned(self):
        """返回值中应包含 recommendation 字段"""
        result_auto = _invoke(amount=100.0, reason="damaged")
        assert result_auto["recommendation"] == "自动审批"

        result_manual = _invoke(amount=600.0, reason="damaged")
        assert result_manual["recommendation"] == "建议人工复核"
