"""
运营 Dashboard：从 SQLite 聚合真实工单 / 审计数据。
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from app.core.auth import get_current_user
from app.core.logging import get_logger
from app.db.database import AsyncSessionLocal
from app.db.models import AuditLog, LLMUsageRecord, Order, RefundLog, Ticket, TicketStatus
from app.erp.metrics import get_erp_business_metrics

logger = get_logger(__name__)
# 运营数据（成本、失败链路、业务量）必须登录后才能访问。
router = APIRouter(dependencies=[Depends(get_current_user)])

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
            "stream_first_result", "supervisor_router",
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


@router.get("/llm-costs")
async def get_llm_costs(days: int = 7, session_limit: int = 10):
    """Provider-neutral token, latency, failover, and estimated-cost report."""

    days = min(max(days, 1), 90)
    session_limit = min(max(session_limit, 1), 100)
    since = datetime.utcnow() - timedelta(days=days)
    try:
        async with AsyncSessionLocal() as session:
            rows = (
                await session.execute(
                    select(LLMUsageRecord).where(LLMUsageRecord.created_at >= since)
                )
            ).scalars().all()

        daily: dict[str, dict] = {}
        sessions: dict[str, dict] = {}
        providers: dict[str, dict] = {}
        nodes: dict[str, dict] = {}
        prompt_versions: dict[str, dict] = {}

        def bucket(store: dict[str, dict], key: str) -> dict:
            return store.setdefault(
                key,
                {
                    "calls": 0,
                    "failed_calls": 0,
                    "fallback_calls": 0,
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                    "estimated_cost_usd": 0.0,
                    "latency_ms": 0,
                },
            )

        for row in rows:
            targets = (
                bucket(daily, row.created_at.strftime("%Y-%m-%d")),
                bucket(sessions, row.thread_id),
                bucket(providers, f"{row.provider}:{row.model}"),
                bucket(nodes, row.node_name),
                bucket(
                    prompt_versions,
                    f"{row.node_name}:{row.prompt_version or 'unknown'}:{row.prompt_variant or 'stable'}",
                ),
            )
            for target in targets:
                target["calls"] += 1
                target["failed_calls"] += 0 if row.success else 1
                target["fallback_calls"] += 1 if row.fallback_index > 0 else 0
                target["prompt_tokens"] += row.prompt_tokens or 0
                target["completion_tokens"] += row.completion_tokens or 0
                target["total_tokens"] += row.total_tokens or 0
                target["estimated_cost_usd"] += float(row.total_cost_usd or 0)
                target["latency_ms"] += row.latency_ms or 0

        def records(store: dict[str, dict], label: str) -> list[dict]:
            result = []
            for key, value in store.items():
                calls = value["calls"]
                total_latency = value["latency_ms"]
                public_value = {item_key: item_value for item_key, item_value in value.items() if item_key != "latency_ms"}
                result.append({
                    label: key,
                    **public_value,
                    "estimated_cost_usd": round(value["estimated_cost_usd"], 8),
                    "avg_latency_ms": round(total_latency / calls, 1) if calls else 0,
                    "failure_rate": round(value["failed_calls"] / calls, 4) if calls else 0,
                    "fallback_rate": round(value["fallback_calls"] / calls, 4) if calls else 0,
                })
            return result

        daily_rows = sorted(records(daily, "date"), key=lambda item: item["date"])
        session_rows = sorted(
            records(sessions, "thread_id"),
            key=lambda item: item["estimated_cost_usd"],
            reverse=True,
        )[:session_limit]
        provider_rows = sorted(records(providers, "provider_model"), key=lambda item: item["calls"], reverse=True)
        node_rows = sorted(records(nodes, "node"), key=lambda item: item["calls"], reverse=True)
        prompt_version_rows = sorted(
            records(prompt_versions, "node_prompt_variant"),
            key=lambda item: item["calls"],
            reverse=True,
        )
        return {
            "window_days": days,
            "totals": {
                "calls": sum(item["calls"] for item in daily_rows),
                "total_tokens": sum(item["total_tokens"] for item in daily_rows),
                "estimated_cost_usd": round(sum(item["estimated_cost_usd"] for item in daily_rows), 8),
                "failed_calls": sum(item["failed_calls"] for item in daily_rows),
                "fallback_calls": sum(item["fallback_calls"] for item in daily_rows),
            },
            "daily": daily_rows,
            "sessions": session_rows,
            "providers": provider_rows,
            "nodes": node_rows,
            "prompt_versions": prompt_version_rows,
            "pricing": "configurable_estimate",
        }
    except Exception as e:
        logger.error("llm_cost_report_error", error=str(e))
        return {
            "window_days": days,
            "totals": {"calls": 0, "total_tokens": 0, "estimated_cost_usd": 0, "failed_calls": 0, "fallback_calls": 0},
            "daily": [],
            "sessions": [],
            "providers": [],
            "nodes": [],
            "prompt_versions": [],
            "pricing": "configurable_estimate",
            "error": str(e),
        }


@router.get("/erp-business-metrics")
async def get_erp_metrics(days: int = 7):
    """Return connector reliability, business outcomes, and SLO status."""

    try:
        return await get_erp_business_metrics(days=days)
    except Exception as e:
        logger.error("erp_business_metrics_error", error=str(e))
        return {
            "window_days": days,
            "connector_executions": {},
            "business_outcomes": {},
            "operations": [],
            "slo": {"met": False, "error": str(e)},
        }


@router.get("/agent-slo")
async def get_agent_slo(hours: int = 24):
    """A5: Agent 侧质量 SLO——路由分布/HITL/节点错误率/LLM 链路健康。

    与 /erp-business-metrics 的 ERP SLO 对齐，让 agent 的质量同样可度量。
    """
    from app.agent.agent_slo import compute_agent_slo

    try:
        async with AsyncSessionLocal() as session:
            return await compute_agent_slo(session, hours=max(1, min(hours, 24 * 30)))
    except Exception as e:
        logger.error("agent_slo_error", error=str(e))
        return {"window_hours": hours, "error": str(e)}


@router.get("/chain-health")
async def get_chain_health(hours: int = 24):
    """F5（agent×数据结合层）：一个响应里同时给出 agent 侧 SLO 与 ERP 业务指标，
    并用共享 trace 抽样把两层缝合起来——让"用户请求 → 财务落地"这条完整链路
    的健康度可以在一个视图里看，而不是 agent 一个 dashboard、ERP 另一个。"""
    from app.agent.agent_slo import compute_agent_slo
    from sqlalchemy import func, select
    from app.db.models import AuditLog

    window = max(1, min(hours, 24 * 30))
    result: dict = {"window_hours": window}

    try:
        async with AsyncSessionLocal() as session:
            result["agent"] = await compute_agent_slo(session, hours=window)
    except Exception as e:
        logger.error("chain_health_agent_error", error=str(e))
        result["agent"] = {"error": str(e)}

    try:
        result["erp"] = await get_erp_business_metrics(days=max(1, window // 24 or 1))
    except Exception as e:
        logger.error("chain_health_erp_error", error=str(e))
        result["erp"] = {"error": str(e)}

    # 缝合指标：F2 让 ERP 连接器审计与 agent 主链路共享 trace_id。这里统计
    # 有多少 ERP 连接器调用能被 join 回 agent 发起的 trace（非 erp: 前缀的
    # thread_id 即来自 agent），用作"两层是否真正贯穿"的可观测证据。
    try:
        from datetime import datetime, timedelta

        since = datetime.utcnow() - timedelta(hours=window)
        async with AsyncSessionLocal() as session:
            total_erp = await session.scalar(
                select(func.count(AuditLog.id)).where(
                    AuditLog.created_at >= since,
                    AuditLog.node_name == "erp_connector",
                )
            )
            joinable_erp = await session.scalar(
                select(func.count(AuditLog.id)).where(
                    AuditLog.created_at >= since,
                    AuditLog.node_name == "erp_connector",
                    ~AuditLog.thread_id.like("erp:%"),
                )
            )
        total_i = int(total_erp or 0)
        joinable_i = int(joinable_erp or 0)
        result["linkage"] = {
            "erp_connector_calls": total_i,
            "agent_joinable_calls": joinable_i,
            "trace_join_rate": round(joinable_i / total_i, 4) if total_i else 0.0,
        }
    except Exception as e:
        logger.error("chain_health_linkage_error", error=str(e))
        result["linkage"] = {"error": str(e)}

    return result
