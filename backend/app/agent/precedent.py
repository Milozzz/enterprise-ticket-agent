"""G1: 判例检索式风控（precedent-based decisioning）。

核心思想：审批表里躺着标注好的历史判决——每条 ApprovalTask 带金额/风险分
（business_payload）、人工结论（status=approved/rejected）、拒绝理由
（reviewer_comment）。风控时检索"同租户、同类型、金额相近"的历史人工决策，
把批准率和典型拒绝理由变成两样东西：

1. 风控评分输入：类似案件历史批准率极低 → 加分并强制人工；
   批准率极高且样本充分 → 小幅减分（判例支持，但绝不解除既有强制人工条件）。
2. 审批人参考：把判例摘要放进 approval panel 与收件箱 payload，
   审批人看到"过去 90 天 17 件类似案件，批准率 12%，常见拒绝理由：xxx"。

零 LLM、零新表、纯 SQL + 内存过滤；每一次新审批自动扩充判例库（飞轮）。
失败静默返回 None——判例是增强信号，绝不阻断主流程。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select

from app.agent.dependencies import resolve_session_factory
from app.core.logging import get_logger
from app.db.database import AsyncSessionLocal
from app.db.models import ApprovalTask
from app.db.tenant_context import tenant_scope

logger = get_logger(__name__)

# 检索窗口与相似度参数
PRECEDENT_WINDOW_DAYS = 90
AMOUNT_SIMILARITY_RATIO = 0.30  # 金额 ±30% 视为"类似案件"
MAX_CANDIDATES = 300            # 单次最多回读的已决审批数
MIN_SAMPLE_SIZE = 5             # 样本低于此数不产生评分影响（只作展示）
STRONG_SAMPLE_SIZE = 10         # 减分（判例支持）要求的更高样本门槛


def _as_decimal(value: Any) -> Decimal | None:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return amount if amount > 0 else None


async def load_refund_precedents(
    *,
    tenant_id: str,
    amount: Any,
    requester_id: str | None = None,
    approval_type: str = "refund",
    session_factory=None,
) -> dict[str, Any] | None:
    """检索类似历史审批并汇总为判例摘要。失败或无可比金额时返回 None。"""

    target_amount = _as_decimal(amount)
    if target_amount is None:
        return None
    low = target_amount * (1 - Decimal(str(AMOUNT_SIMILARITY_RATIO)))
    high = target_amount * (1 + Decimal(str(AMOUNT_SIMILARITY_RATIO)))
    since = datetime.utcnow() - timedelta(days=PRECEDENT_WINDOW_DAYS)

    try:
        factory = resolve_session_factory(session_factory or AsyncSessionLocal)
        with tenant_scope(tenant_id):
            async with factory() as session:
                rows = (
                    await session.execute(
                        select(
                            ApprovalTask.status,
                            ApprovalTask.business_payload,
                            ApprovalTask.reviewer_comment,
                            ApprovalTask.requester_id,
                            ApprovalTask.completed_at,
                        )
                        .where(
                            ApprovalTask.tenant_id == tenant_id,
                            ApprovalTask.approval_type == approval_type,
                            ApprovalTask.status.in_(["approved", "rejected"]),
                            ApprovalTask.completed_at.isnot(None),
                            ApprovalTask.completed_at >= since,
                        )
                        .order_by(ApprovalTask.completed_at.desc())
                        .limit(MAX_CANDIDATES)
                    )
                ).all()
    except Exception as exc:
        logger.warning("precedent_lookup_failed", error=str(exc), tenant_id=tenant_id)
        return None

    similar: list[tuple[str, str | None, str | None]] = []  # (status, comment, requester)
    same_user_rejected = 0
    for status_value, payload, comment, req_id, _completed in rows:
        payload = payload if isinstance(payload, dict) else {}
        row_amount = _as_decimal(payload.get("amount"))
        if row_amount is None or not (low <= row_amount <= high):
            continue
        similar.append((str(status_value), comment, str(req_id or "")))
        if (
            requester_id
            and str(req_id or "") == str(requester_id)
            and str(status_value) == "rejected"
        ):
            same_user_rejected += 1

    sample = len(similar)
    if sample == 0:
        return None

    approved = sum(1 for status_value, _, _ in similar if status_value == "approved")
    rejection_comments = [
        comment.strip()
        for status_value, comment, _ in similar
        if status_value == "rejected" and comment and comment.strip()
    ]
    # 去重保序，最多 3 条典型拒绝理由
    seen: set[str] = set()
    top_rejections: list[str] = []
    for comment in rejection_comments:
        key = comment[:80]
        if key not in seen:
            seen.add(key)
            top_rejections.append(comment[:120])
        if len(top_rejections) >= 3:
            break

    return {
        "window_days": PRECEDENT_WINDOW_DAYS,
        "amount_band": [str(low.quantize(Decimal('0.01'))), str(high.quantize(Decimal('0.01')))],
        "sample_size": sample,
        "approved": approved,
        "rejected": sample - approved,
        "approval_rate": round(approved / sample, 4),
        "same_user_rejected_similar": same_user_rejected,
        "top_rejection_comments": top_rejections,
        "scoring_eligible": sample >= MIN_SAMPLE_SIZE,
        "strong_sample": sample >= STRONG_SAMPLE_SIZE,
    }


def apply_precedents_to_risk(risk_data: dict, precedents: dict[str, Any] | None) -> None:
    """把判例摘要作用到风控评分（原地修改 risk_data）。

    规则刻意保守：
    - 加分/强制人工：批准率 ≤20% 或同用户有类似被拒 → 收紧。
    - 减分：批准率 ≥95% 且样本 ≥10 才 -10 分，且只减分、绝不直接翻转
      autoApprove——已有的强制人工条件（欺诈标记/对账差异/policy 命中）不受影响，
      因为它们都在减分之后重新落 autoApprove=False。
    - 样本 <5 只展示不评分，避免小样本噪声。
    """
    if not precedents:
        return
    risk_data["precedents"] = precedents
    if not precedents.get("scoring_eligible"):
        return

    rate = float(precedents.get("approval_rate") or 0.0)
    sample = int(precedents.get("sample_size") or 0)

    if precedents.get("same_user_rejected_similar", 0) > 0:
        risk_data["autoApprove"] = False
        risk_data.setdefault("reasons", []).append(
            f"判例：该用户此前有 {precedents['same_user_rejected_similar']} 件类似金额申请被人工拒绝，强制人工复核"
        )
    if rate <= 0.2:
        risk_data["riskScore"] = min(100, risk_data.get("riskScore", 0) + 20)
        risk_data["autoApprove"] = False
        reason = (
            f"判例：近 {precedents['window_days']} 天 {sample} 件类似案件"
            f"人工批准率仅 {rate:.0%}"
        )
        if precedents.get("top_rejection_comments"):
            reason += f"（常见拒绝理由：{precedents['top_rejection_comments'][0]}）"
        risk_data.setdefault("reasons", []).append(reason)
    elif rate >= 0.95 and precedents.get("strong_sample"):
        risk_data["riskScore"] = max(0, risk_data.get("riskScore", 0) - 10)
        risk_data.setdefault("reasons", []).append(
            f"判例：近 {precedents['window_days']} 天 {sample} 件类似案件"
            f"人工批准率 {rate:.0%}，判例支持（-10 分）"
        )
