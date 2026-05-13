"""
eval_metrics.py — 从 AuditLog 提炼系统运行指标

用法：
    cd backend
    python -m scripts.eval_metrics

输出：
    - 各节点平均 / P95 耗时
    - 意图分布
    - 失败率
    - LLM 降级触发次数（structured → regex → rule）
    - 端到端完整流程耗时
"""

import asyncio
import statistics
from collections import defaultdict
from datetime import datetime, timedelta

from sqlalchemy import select, func

# 确保能 import app 模块
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db.database import AsyncSessionLocal
from app.db.models import AuditLog, Ticket, TicketStatus


NODE_LABELS = {
    "classify_intent":    "意图识别",
    "lookup_order":       "查询订单",
    "fetch_user_history": "查询用户历史",
    "check_risk":         "风控评估",
    "human_review":       "人工审批",
    "execute_refund":     "执行退款",
    "send_notification":  "发送通知",
    "answer_node":        "生成回复",
    "answer_policy_node": "政策查询(RAG)",
    "summarize_session":  "会话摘要",
}

NODE_ORDER = list(NODE_LABELS.keys())


def percentile(sorted_vals: list[float], p: float) -> float:
    if not sorted_vals:
        return 0.0
    idx = min(int(len(sorted_vals) * p), len(sorted_vals) - 1)
    return sorted_vals[idx]


def section(title: str):
    width = 60
    print(f"\n{'═' * width}")
    print(f"  {title}")
    print(f"{'═' * width}")


async def run():
    since = datetime.utcnow() - timedelta(days=30)

    async with AsyncSessionLocal() as session:

        # ── 1. 节点耗时分析 ────────────────────────────────────────────
        section("节点耗时分析（近 30 天，成功执行）")

        stmt = select(AuditLog.node_name, AuditLog.duration_ms).where(
            AuditLog.duration_ms.isnot(None),
            AuditLog.success == True,
            AuditLog.created_at >= since,
        )
        rows = (await session.execute(stmt)).all()

        buckets: dict[str, list[float]] = defaultdict(list)
        for node_name, ms in rows:
            buckets[node_name].append(float(ms))

        all_nodes = list(dict.fromkeys(NODE_ORDER + list(buckets.keys())))
        has_data = False
        for node in all_nodes:
            vals = sorted(buckets.get(node, []))
            if not vals:
                continue
            has_data = True
            avg = statistics.mean(vals)
            p95 = percentile(vals, 0.95)
            label = NODE_LABELS.get(node, node)
            print(f"  {label:<20} n={len(vals):>4}  avg={avg:>7.0f}ms  P95={p95:>7.0f}ms")

        if not has_data:
            print("  暂无数据，请先运行几次对话再执行此脚本")

        # ── 2. 失败率分析 ──────────────────────────────────────────────
        section("节点失败率（近 30 天）")

        total_stmt = (
            select(AuditLog.node_name, func.count().label("total"))
            .where(AuditLog.created_at >= since)
            .group_by(AuditLog.node_name)
        )
        fail_stmt = (
            select(AuditLog.node_name, func.count().label("fails"))
            .where(AuditLog.success == False, AuditLog.created_at >= since)
            .group_by(AuditLog.node_name)
        )
        total_rows = {r.node_name: r.total for r in (await session.execute(total_stmt)).all()}
        fail_rows  = {r.node_name: r.fails  for r in (await session.execute(fail_stmt)).all()}

        has_fail_data = False
        for node in all_nodes:
            total = total_rows.get(node, 0)
            if not total:
                continue
            has_fail_data = True
            fails = fail_rows.get(node, 0)
            rate  = fails / total * 100
            label = NODE_LABELS.get(node, node)
            bar   = "█" * int(rate / 5) if rate > 0 else "·"
            print(f"  {label:<20} {fails:>3}/{total:<4} = {rate:>5.1f}%  {bar}")

        if not has_fail_data:
            print("  暂无数据")

        # ── 3. 意图分布 ────────────────────────────────────────────────
        section("意图分布（近 30 天，classify_intent 节点输出）")

        intent_stmt = select(AuditLog.output_data).where(
            AuditLog.node_name == "classify_intent",
            AuditLog.success == True,
            AuditLog.created_at >= since,
        )
        intent_rows = (await session.execute(intent_stmt)).scalars().all()

        intent_counter: dict[str, int] = defaultdict(int)
        for output in intent_rows:
            if isinstance(output, dict):
                intent = output.get("intent", "unknown")
                intent_counter[intent] += 1

        total_intents = sum(intent_counter.values())
        if total_intents:
            for intent, cnt in sorted(intent_counter.items(), key=lambda x: -x[1]):
                pct = cnt / total_intents * 100
                bar = "█" * int(pct / 5)
                print(f"  {intent:<20} {cnt:>4} 次  {pct:>5.1f}%  {bar}")
        else:
            print("  暂无数据（output_data 中未找到 intent 字段）")

        # ── 4. LLM 降级分析 ────────────────────────────────────────────
        section("LLM 降级触发分析（classify_intent，近 30 天）")

        method_stmt = select(AuditLog.output_data).where(
            AuditLog.node_name == "classify_intent",
            AuditLog.success == True,
            AuditLog.created_at >= since,
        )
        method_rows = (await session.execute(method_stmt)).scalars().all()

        method_counter: dict[str, int] = defaultdict(int)
        for output in method_rows:
            if isinstance(output, dict):
                method = output.get("_method", "unknown")
                method_counter[method] += 1

        total_methods = sum(method_counter.values())
        method_labels = {
            "llm_structured": "结构化 JSON 输出（主路径）",
            "llm_regex":      "正则提取（一级降级）",
            "rules":          "规则引擎（二级降级）",
            "unknown":        "未记录",
        }
        if total_methods:
            for method in ["llm_structured", "llm_regex", "rules", "unknown"]:
                cnt = method_counter.get(method, 0)
                if not cnt:
                    continue
                pct  = cnt / total_methods * 100
                label = method_labels.get(method, method)
                print(f"  {label:<30} {cnt:>4} 次  {pct:>5.1f}%")
        else:
            print("  暂无降级数据（_method 字段未写入 output_data）")

        # ── 5. 端到端耗时（退款流程）──────────────────────────────────
        section("端到端耗时（完整退款流程，近 30 天）")

        # 按 thread_id 聚合所有节点耗时之和
        e2e_stmt = (
            select(AuditLog.thread_id, func.sum(AuditLog.duration_ms).label("total_ms"))
            .where(
                AuditLog.duration_ms.isnot(None),
                AuditLog.created_at >= since,
            )
            .group_by(AuditLog.thread_id)
        )
        e2e_rows = (await session.execute(e2e_stmt)).all()

        e2e_vals = sorted([float(r.total_ms) for r in e2e_rows if r.total_ms and r.total_ms > 0])
        if e2e_vals:
            avg_e2e = statistics.mean(e2e_vals)
            p95_e2e = percentile(e2e_vals, 0.95)
            print(f"  样本数：{len(e2e_vals)}")
            print(f"  平均端到端耗时：{avg_e2e/1000:.1f} 秒")
            print(f"  P95 端到端耗时：{p95_e2e/1000:.1f} 秒")
            print(f"  最快：{e2e_vals[0]/1000:.1f}s  最慢：{e2e_vals[-1]/1000:.1f}s")
        else:
            print("  暂无数据")

        # ── 6. 工单状态分布 ────────────────────────────────────────────
        section("工单状态分布（全量）")

        for status in TicketStatus:
            cnt = await session.scalar(
                select(func.count()).select_from(Ticket).where(Ticket.status == status)
            ) or 0
            print(f"  {status.value:<20} {cnt} 张")

        # ── 7. 审计事件总量 ────────────────────────────────────────────
        section("审计日志概览")

        total_logs = await session.scalar(select(func.count()).select_from(AuditLog)) or 0
        logs_30d   = await session.scalar(
            select(func.count()).select_from(AuditLog).where(AuditLog.created_at >= since)
        ) or 0
        logs_24h   = await session.scalar(
            select(func.count()).select_from(AuditLog).where(
                AuditLog.created_at >= datetime.utcnow() - timedelta(hours=24)
            )
        ) or 0
        threads_30d = await session.scalar(
            select(func.count(func.distinct(AuditLog.thread_id))).where(
                AuditLog.created_at >= since
            )
        ) or 0

        print(f"  总日志条数：{total_logs}")
        print(f"  近 30 天：{logs_30d} 条 / {threads_30d} 个 thread")
        print(f"  近 24h：{logs_24h} 条")

    print(f"\n{'─' * 60}")
    print("  ✓ 评估完成，将上方数字整理到简历和面试准备材料中")
    print(f"{'─' * 60}\n")


if __name__ == "__main__":
    asyncio.run(run())
