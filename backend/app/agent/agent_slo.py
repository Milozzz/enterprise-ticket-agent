"""A5: Agent 侧质量 SLO 聚合——让 agent 的"好坏"可度量、可对齐 ERP 侧的 SLO。

指标口径（时间窗内）：
- 路由质量：supervisor 路由分布、语义路由占比、低置信澄清率
- HITL：审批触发数 / 完成数 / 超时数 / 平均决策时长
- 工具与节点健康：节点错误率（audit_logs 中带 error_message 的输出占比）
- LLM 链路：调用成功率、failover 触发率、降级决策次数、平均时延、token 用量

数据全部来自既有表（audit_logs / approval_tasks / llm_usage_records），
不引入新表；JSON 字段的解析在 Python 侧完成（demo 规模足够，
数据量大后可下推到 SQL/物化视图）。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select

from app.core.logging import get_logger
from app.db.models import ApprovalTask, AuditLog, LLMUsageRecord

logger = get_logger(__name__)

# 单次聚合最多回读的审计行数（防止窗口过大拖垮接口）
_MAX_AUDIT_ROWS = 5000


async def compute_agent_slo(session, *, hours: int = 24) -> dict[str, Any]:
    """聚合 agent 侧 SLO。session 为 AsyncSession（依赖注入传入）。"""
    since = datetime.utcnow() - timedelta(hours=max(1, hours))

    # ── 1) 审计事件（节点健康 + 路由分布 + 降级次数）─────────────────────
    audit_rows = (
        await session.execute(
            select(AuditLog.node_name, AuditLog.output_data)
            .where(AuditLog.created_at >= since)
            .order_by(AuditLog.id.desc())
            .limit(_MAX_AUDIT_ROWS)
        )
    ).all()

    node_total = 0
    node_errors = 0
    degraded_events = 0
    routing_total = 0
    routing_by_scenario: dict[str, int] = {}
    routing_semantic = 0
    routing_clarifications = 0

    for node_name, output in audit_rows:
        output = output if isinstance(output, dict) else {}
        node_total += 1
        if output.get("error_message"):
            node_errors += 1
        if output.get("llm_degraded"):
            degraded_events += 1
        if node_name == "supervisor_router":
            decision = output.get("supervisor_decision") or {}
            if isinstance(decision, dict) and decision.get("scenario_id"):
                routing_total += 1
                sid = str(decision.get("scenario_id"))
                routing_by_scenario[sid] = routing_by_scenario.get(sid, 0) + 1
                if decision.get("routing_method") == "llm_semantic":
                    routing_semantic += 1
                if decision.get("needs_clarification"):
                    routing_clarifications += 1

    # ── 2) HITL 审批 ─────────────────────────────────────────────────────
    approvals = (
        await session.execute(
            select(
                ApprovalTask.status,
                ApprovalTask.created_at,
                ApprovalTask.completed_at,
                ApprovalTask.due_at,
            ).where(ApprovalTask.created_at >= since)
        )
    ).all()
    hitl_created = len(approvals)
    hitl_completed = 0
    hitl_overdue = 0
    decision_minutes: list[float] = []
    now = datetime.utcnow()
    for status_value, created_at, completed_at, due_at in approvals:
        if status_value in {"approved", "rejected"}:
            hitl_completed += 1
            if completed_at and created_at:
                decision_minutes.append(
                    max(0.0, (completed_at - created_at).total_seconds() / 60)
                )
        elif status_value == "pending" and due_at and due_at < now:
            hitl_overdue += 1

    # ── 3) LLM 链路 ──────────────────────────────────────────────────────
    llm_rows = (
        await session.execute(
            select(
                func.count(LLMUsageRecord.id),
                func.sum(LLMUsageRecord.total_tokens),
                func.avg(LLMUsageRecord.latency_ms),
            ).where(LLMUsageRecord.created_at >= since)
        )
    ).one()
    llm_total = int(llm_rows[0] or 0)
    llm_tokens = int(llm_rows[1] or 0)
    llm_avg_latency = float(llm_rows[2] or 0.0)
    llm_success = int(
        (
            await session.execute(
                select(func.count(LLMUsageRecord.id)).where(
                    LLMUsageRecord.created_at >= since,
                    LLMUsageRecord.success.is_(True),
                )
            )
        ).scalar()
        or 0
    )
    llm_failover = int(
        (
            await session.execute(
                select(func.count(LLMUsageRecord.id)).where(
                    LLMUsageRecord.created_at >= since,
                    LLMUsageRecord.fallback_index > 0,
                )
            )
        ).scalar()
        or 0
    )

    def _rate(numerator: int, denominator: int) -> float:
        return round(numerator / denominator, 4) if denominator else 0.0

    return {
        "window_hours": hours,
        "computed_at": now.isoformat(),
        "routing": {
            "total": routing_total,
            "by_scenario": routing_by_scenario,
            "semantic_route_rate": _rate(routing_semantic, routing_total),
            "clarification_rate": _rate(routing_clarifications, routing_total),
        },
        "hitl": {
            "created": hitl_created,
            "completed": hitl_completed,
            "overdue_pending": hitl_overdue,
            "avg_decision_minutes": (
                round(sum(decision_minutes) / len(decision_minutes), 1)
                if decision_minutes
                else None
            ),
        },
        "nodes": {
            "events": node_total,
            "error_rate": _rate(node_errors, node_total),
            "degraded_decisions": degraded_events,
        },
        "llm": {
            "calls": llm_total,
            "success_rate": _rate(llm_success, llm_total),
            "failover_rate": _rate(llm_failover, llm_total),
            "avg_latency_ms": round(llm_avg_latency, 1),
            "total_tokens": llm_tokens,
        },
        "audit_rows_scanned": len(audit_rows),
        "audit_rows_capped": len(audit_rows) >= _MAX_AUDIT_ROWS,
    }
