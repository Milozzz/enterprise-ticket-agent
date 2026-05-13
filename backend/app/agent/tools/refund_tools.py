"""
退款执行工具 (Function Calling Tools)
使用 Pydantic + Field(description) 确保 LLM 准确理解参数含义
"""

from langchain_core.tools import tool
from pydantic import BaseModel, Field
from app.core.config import get_settings
from app.db.ticket_repository import deterministic_refund_id
from datetime import datetime

settings = get_settings()


# ── Input Schemas ─────────────────────────────────────────────────
class CheckRiskLevelInput(BaseModel):
    order_id: str = Field(
        description="订单号，纯数字字符串，例如 '789012'"
    )
    amount: float = Field(
        description="退款金额（人民币），必须大于 0，例如 1299.0",
        gt=0,
    )
    user_id: str = Field(
        description="发起退款请求的用户 ID，用于判断历史退款记录风险"
    )
    reason: str = Field(
        description=(
            "退款原因代码，可选值：'damaged'（商品破损）、'wrong_item'（发错货）、"
            "'not_received'（未收到）、'quality_issue'（质量问题）、'other'（其他）"
        )
    )


class ExecuteRefundInput(BaseModel):
    order_id: str = Field(
        description="需要退款的订单号"
    )
    amount: float = Field(
        description="实际退款金额（人民币），应与订单金额一致",
        gt=0,
    )
    ticket_id: str = Field(
        description="对应的工单 ID，用作幂等键防止重复退款"
    )


# ── Tools ─────────────────────────────────────────────────────────
@tool(args_schema=CheckRiskLevelInput)
def check_risk_level(order_id: str, amount: float, user_id: str, reason: str) -> dict:
    """
    对退款申请进行风险评估，返回风险分数和是否需要人工审批的判断。
    风险分数 >= 40 时需要人工审批，< 40 时自动通过。
    必须在 execute_refund 之前调用。
    """
    risk_score = 0
    reasons = []
    threshold = settings.risk_threshold_amount

    if amount > threshold:
        risk_score += 40
        reasons.append(f"退款金额 ¥{amount} 超过阈值 ¥{threshold}")

    if amount > 1000:
        risk_score += 20
        reasons.append(f"高额退款（¥{amount}），需要额外审核")

    if reason in ("not_received", "other"):
        risk_score += 20
        reasons.append(f"退款原因「{reason}」存在一定风险")

    if user_id == "user_demo":
        risk_score += 10
        reasons.append("用户近期有多次退款记录")

    risk_score = min(risk_score, 100)

    if risk_score >= 60:
        risk_level = "high"
    elif risk_score >= 30:
        risk_level = "medium"
    else:
        risk_level = "low"

    auto_approve = risk_score < 40

    return {
        "riskScore": risk_score,
        "riskLevel": risk_level,
        "reasons": reasons if reasons else ["未发现明显风险因素"],
        "autoApprove": auto_approve,
        "threshold": threshold,
        "recommendation": "自动审批" if auto_approve else "建议人工复核",
    }


@tool(args_schema=ExecuteRefundInput)
def execute_refund(order_id: str, amount: float, ticket_id: str) -> dict:
    """
    执行退款操作，将退款金额退还至用户原支付账户。
    仅在风险评估通过（或人工审批批准）后才能调用。
    返回退款单号和预计到账天数。
    """
    refund_id = deterministic_refund_id(order_id, ticket_id, amount)

    return {
        "success": True,
        "refundId": refund_id,
        "idempotencyKey": f"refund:{order_id}:{ticket_id}:{amount:.2f}",
        "amount": amount,
        "estimatedDays": 3,
        "message": (
            f"退款 ¥{amount} 已提交，预计 3 个工作日内到账"
            f"（退款单号：{refund_id}）"
        ),
    }
