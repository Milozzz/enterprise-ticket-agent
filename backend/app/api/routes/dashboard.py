"""
运营 Dashboard：从 SQLite 聚合真实工单 / 审计数据。
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta

from fastapi import APIRouter
from sqlalchemy import func, select
from app.core.logging import get_logger
from app.db.database import AsyncSessionLocal
from app.db.models import AuditLog, Order, RefundLog, Ticket, TicketStatus

logger = get_logger(__name__)
router = APIRouter()

# 人工审批超时阈值（超过此时长仍处于 PENDING 视为超时）
APPROVAL_TIMEOUT_HOURS = 24


def _mock_stats() -> dict:
    """数据库不可用时返回占位结构，避免前端崩溃"""
    return {
        "totalTickets": 0,
        "autoResolvedRate": 0.0,
        "avgProcessingTimeMinutes": 0.0,
        "riskInterceptedCount": 0,
        "costSavedAmount": 0.0,
        "ticketsByStatus": {
            "pending": 0,
            "processing": 0,
            "awaiting_approval": 0,
            "approved": 0,
            "rejected": 0,
            "completed": 0,
            "escalated": 0,
        },
        "dailyTrend": [
            {
                "date": (datetime.utcnow() - timedelta(days=6 - i)).strftime("%m/%d"),
                "count": 0,
                "autoResolved": 0,
            }
            for i in range(7)
        ],
        "auditEvents24h": 0,
        "source": "fallback",
    }


async def _compute_stats() -> dict:
    async with AsyncSessionLocal() as session:
        total = int(await session.scalar(select(func.count()).select_from(Ticket)) or 0)

        pending = int(
            await session.scalar(
                select(func.count()).select_from(Ticket).where(Ticket.status == TicketStatus.PENDING)
            )
            or 0
        )
        approved = int(
            await session.scalar(
                select(func.count()).select_from(Ticket).where(Ticket.status == TicketStatus.APPROVED)
            )
            or 0
        )
        rejected = int(
            await session.scalar(
                select(func.count()).select_from(Ticket).where(Ticket.status == TicketStatus.REJECTED)
            )
            or 0
        )
        completed = int(
            await session.scalar(
                select(func.count()).select_from(Ticket).where(Ticket.status == TicketStatus.COMPLETED)
            )
            or 0
        )

        # 与前端 TicketStatus 枚举对齐（DB 仅四态，做业务语义映射）
        tickets_by_status = {
            "pending": 0,
            "processing": approved,
            "awaiting_approval": pending,
            "approved": 0,
            "rejected": rejected,
            "completed": completed,
            "escalated": 0,
        }

        auto_resolved_rate = (completed / total) if total else 0.0

        stmt_sum = (
            select(func.coalesce(func.sum(Order.amount), 0.0))
            .select_from(Ticket)
            .join(Order, Ticket.order_id == Order.id)
            .where(Ticket.status == TicketStatus.COMPLETED)
        )
        cost_saved = float(await session.scalar(stmt_sum) or 0.0)

        # 平均处理时长：已完成且有退款日志的工单（processed_at - created_at）
        stmt_pairs = (
            select(Ticket, RefundLog)
            .join(RefundLog, RefundLog.ticket_id == Ticket.id)
            .where(Ticket.status == TicketStatus.COMPLETED)
        )
        result = await session.execute(stmt_pairs)
        rows = result.all()
        deltas_min: list[float] = []
        for t, rl in rows:
            if t.created_at and rl.processed_at:
                delta = (rl.processed_at - t.created_at).total_seconds() / 60.0
                if delta >= 0:
                    deltas_min.append(delta)
        avg_min = sum(deltas_min) / len(deltas_min) if deltas_min else 0.0

        # 曾进入风控节点的不重复会话数（近 30 天）
        since = datetime.utcnow() - timedelta(days=30)
        risk_stmt = select(func.count(func.distinct(AuditLog.thread_id))).where(
            AuditLog.node_name == "check_risk",
            AuditLog.created_at >= since,
        )
        risk_intercepted = int(await session.scalar(risk_stmt) or 0)

        # 近 24h 审计事件量
        since24 = datetime.utcnow() - timedelta(hours=24)
        audit24 = select(func.count()).select_from(AuditLog).where(AuditLog.created_at >= since24)
        audit_events_24h = int(await session.scalar(audit24) or 0)

        # 近 7 日按天创建工单数（内存聚合，数据量可控）
        week_ago = datetime.utcnow() - timedelta(days=7)
        stmt_recent = select(Ticket.created_at).where(Ticket.created_at >= week_ago)
        dates = (await session.scalars(stmt_recent)).all()
        by_day: dict[str, int] = defaultdict(int)
        for dt in dates:
            if dt:
                key = dt.strftime("%m/%d")
                by_day[key] += 1

        daily_trend = []
        for i in range(7):
            d = datetime.utcnow() - timedelta(days=6 - i)
            key = d.strftime("%m/%d")
            cnt = by_day.get(key, 0)
            auto = int(cnt * auto_resolved_rate) if cnt else 0
            daily_trend.append({"date": key, "count": cnt, "autoResolved": auto})

        # 超时待审批工单（PENDING 超过 24h）
        timeout_threshold = datetime.utcnow() - timedelta(hours=APPROVAL_TIMEOUT_HOURS)
        timeout_stmt = select(func.count()).select_from(Ticket).where(
            Ticket.status == TicketStatus.PENDING,
            Ticket.created_at <= timeout_threshold,
        )
        approval_timeout_count = int(await session.scalar(timeout_stmt) or 0)

        # 失败节点（近 7 天 success=False 的审计条目，按 node_name 聚合）
        failed_nodes_stmt = (
            select(AuditLog.node_name, func.count().label("cnt"))
            .where(
                AuditLog.success == False,  # noqa: E712
                AuditLog.created_at >= week_ago,
            )
            .group_by(AuditLog.node_name)
            .order_by(func.count().desc())
        )
        failed_node_rows = (await session.execute(failed_nodes_stmt)).all()
        failed_nodes_summary = [{"node": r.node_name, "count": r.cnt} for r in failed_node_rows]

        return {
            "totalTickets": total,
            "autoResolvedRate": round(auto_resolved_rate, 4),
            "avgProcessingTimeMinutes": round(avg_min, 2),
            "riskInterceptedCount": risk_intercepted,
            "costSavedAmount": round(cost_saved, 2),
            "ticketsByStatus": tickets_by_status,
            "dailyTrend": daily_trend,
            "auditEvents24h": audit_events_24h,
            "approvalTimeoutCount": approval_timeout_count,
            "failedNodesSummary": failed_nodes_summary,
            "source": "database",
        }


@router.get("/node-latency")
async def get_node_latency():
    """
    各节点平均/P50/P95 耗时（基于 AuditLog.duration_ms，近 7 天成功记录）。
    供 Dashboard 「节点耗时」图表使用。
    """
    try:
        week_ago = datetime.utcnow() - timedelta(days=7)
        async with AsyncSessionLocal() as session:
            stmt = (
                select(AuditLog.node_name, AuditLog.duration_ms, AuditLog.success, AuditLog.output_data)
                .where(
                    AuditLog.duration_ms.isnot(None),
                    AuditLog.created_at >= week_ago,
                )
                .order_by(AuditLog.node_name)
            )
            rows = (await session.execute(stmt)).all()

        # 按节点分组，计算统计值
        from collections import defaultdict
        import statistics

        buckets: dict[str, list[float]] = defaultdict(list)
        totals: dict[str, dict[str, float]] = defaultdict(lambda: {
            "total": 0,
            "failed": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        })

        def _token_usage(output_data: dict | None) -> dict:
            if not isinstance(output_data, dict):
                return {}
            usage = (
                output_data.get("token_usage")
                or output_data.get("usage_metadata")
                or output_data.get("usage")
                or {}
            )
            if not isinstance(usage, dict):
                return {}
            prompt = usage.get("prompt_tokens") or usage.get("input_tokens") or 0
            completion = usage.get("completion_tokens") or usage.get("output_tokens") or 0
            total = usage.get("total_tokens") or usage.get("total_token_count") or prompt + completion
            return {
                "prompt_tokens": int(prompt or 0),
                "completion_tokens": int(completion or 0),
                "total_tokens": int(total or 0),
            }

        for node_name, ms, success, output_data in rows:
            totals[node_name]["total"] += 1
            if success is False:
                totals[node_name]["failed"] += 1
            if ms is not None:
                buckets[node_name].append(float(ms))
            usage = _token_usage(output_data)
            totals[node_name]["prompt_tokens"] += usage.get("prompt_tokens", 0)
            totals[node_name]["completion_tokens"] += usage.get("completion_tokens", 0)
            totals[node_name]["total_tokens"] += usage.get("total_tokens", 0)

        result = []
        NODE_ORDER = [
            "classify_intent", "lookup_order", "fetch_user_history",
            "check_risk", "human_review", "execute_refund",
            "send_notification", "answer_node", "summarize_session",
        ]
        all_nodes = list(dict.fromkeys(NODE_ORDER + list(buckets.keys())))

        for node in all_nodes:
            vals = sorted(buckets.get(node, []))
            stat = totals.get(node)
            if not vals and not stat:
                continue
            vals = vals or [0.0]
            n = len(vals)
            total_count = int(stat["total"]) if stat else n
            failed_count = int(stat["failed"]) if stat else 0
            result.append({
                "node": node,
                "count": total_count,
                "avg_ms": round(statistics.mean(vals), 1),
                "p50_ms": round(vals[int(n * 0.50)], 1),
                "p95_ms": round(vals[min(int(n * 0.95), n - 1)], 1),
                "max_ms": round(vals[-1], 1),
                "failure_count": failed_count,
                "failure_rate": round(failed_count / total_count, 4) if total_count else 0.0,
                "prompt_tokens": int(stat["prompt_tokens"]) if stat else 0,
                "completion_tokens": int(stat["completion_tokens"]) if stat else 0,
                "total_tokens": int(stat["total_tokens"]) if stat else 0,
            })

        return result
    except Exception as e:
        logger.error("node_latency_error", error=str(e))
        return []


@router.get("/stats")
async def get_dashboard_stats():
    """运营 Dashboard 统计数据（优先读库，失败时返回空结构）"""
    try:
        return await _compute_stats()
    except Exception as e:
        logger.error("dashboard_stats_error", error=str(e))
        data = _mock_stats()
        data["error"] = str(e)
        return data


@router.get("/failed-traces")
async def get_failed_traces(limit: int = 20):
    """
    返回近 7 天内有失败节点的 thread_id 列表，供「失败链路」快速入口使用。
    每条记录包含：thread_id, trace_id, failed_node, error, created_at
    """
    try:
        week_ago = datetime.utcnow() - timedelta(days=7)
        async with AsyncSessionLocal() as session:
            stmt = (
                select(
                    AuditLog.thread_id,
                    AuditLog.trace_id,
                    AuditLog.node_name,
                    AuditLog.output_data,
                    AuditLog.created_at,
                )
                .where(
                    AuditLog.success == False,  # noqa: E712
                    AuditLog.created_at >= week_ago,
                )
                .order_by(AuditLog.created_at.desc())
                .limit(limit)
            )
            rows = (await session.execute(stmt)).all()
        result = []
        seen: set[str] = set()
        for row in rows:
            if row.thread_id in seen:
                continue
            seen.add(row.thread_id)
            error_msg = None
            if isinstance(row.output_data, dict):
                error_msg = row.output_data.get("error_message")
            result.append({
                "thread_id": row.thread_id,
                "trace_id": row.trace_id,
                "failed_node": row.node_name,
                "error": error_msg,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            })
        return result
    except Exception as e:
        logger.error("failed_traces_error", error=str(e))
        return []
