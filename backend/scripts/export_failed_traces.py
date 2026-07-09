"""A5: 失败 trace 回灌——把线上失败链路导出为回归评测用例。

用法（在 backend 目录下）：
    python -m scripts.export_failed_traces [--hours 168] [--limit 200]

从 audit_logs 中抽取带 error_message 的节点输出，按 thread 聚合去重后，
追加写入 evals/regression_from_traces.json（与 evals/ 下其他数据集同构：
{"cases": [...]}）。同一 (node, error) 组合只保留最新一条，避免同类故障
刷屏。产出的用例可直接被评测脚本消费，实现"线上失败 → 明日回归"闭环。
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import select

EVAL_FILE = Path(__file__).resolve().parents[1] / "evals" / "regression_from_traces.json"


def _case_key(node_name: str, error: str) -> str:
    # 错误信息截断到前 120 字符做去重键：同类错误不同参数只留一条
    return f"{node_name}::{error[:120]}"


async def export(hours: int, limit: int) -> int:
    from app.db.database import AsyncSessionLocal
    from app.db.models import AuditLog

    since = datetime.utcnow() - timedelta(hours=hours)
    async with AsyncSessionLocal() as session:
        rows = (
            await session.execute(
                select(
                    AuditLog.thread_id,
                    AuditLog.trace_id,
                    AuditLog.node_name,
                    AuditLog.input_data,
                    AuditLog.output_data,
                    AuditLog.created_at,
                )
                .where(AuditLog.created_at >= since)
                .order_by(AuditLog.id.desc())
                .limit(limit * 10)
            )
        ).all()

    seen: set[str] = set()
    cases: list[dict] = []
    for thread_id, trace_id, node_name, input_data, output_data, created_at in rows:
        output = output_data if isinstance(output_data, dict) else {}
        error = str(output.get("error_message") or "")
        if not error:
            continue
        key = _case_key(node_name, error)
        if key in seen:
            continue
        seen.add(key)
        user_message = ""
        if isinstance(input_data, dict):
            user_message = str(
                input_data.get("message")
                or input_data.get("user_message")
                or ""
            )[:500]
        cases.append(
            {
                "id": f"trace-{thread_id}-{node_name}",
                "source": "production_trace",
                "thread_id": thread_id,
                "trace_id": trace_id,
                "node": node_name,
                "error_message": error[:500],
                "user_message": user_message,
                "captured_at": created_at.isoformat() if created_at else None,
                # 评测语义：重放该输入时，此节点不应再出现同类 error
                "expectation": "node_completes_without_error",
            }
        )
        if len(cases) >= limit:
            break

    # 与既有文件合并（按 id 去重，新用例优先）
    existing: list[dict] = []
    if EVAL_FILE.exists():
        try:
            existing = list(
                json.loads(EVAL_FILE.read_text(encoding="utf-8")).get("cases") or []
            )
        except (ValueError, OSError):
            existing = []
    new_ids = {case["id"] for case in cases}
    merged = cases + [case for case in existing if case.get("id") not in new_ids]

    EVAL_FILE.parent.mkdir(parents=True, exist_ok=True)
    EVAL_FILE.write_text(
        json.dumps({"cases": merged}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(
        f"Exported {len(cases)} new failure cases "
        f"({len(merged)} total) -> {EVAL_FILE}"
    )
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--hours", type=int, default=168, help="回看窗口（小时）")
    parser.add_argument("--limit", type=int, default=200, help="最多导出的新用例数")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(export(args.hours, args.limit)))
