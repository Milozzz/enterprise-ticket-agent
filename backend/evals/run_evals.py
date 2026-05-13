"""
Evals Framework — enterprise-ticket-agent 分类器自动评测脚本

用法：
  cd backend
  python -m evals.run_evals                      # 使用规则引擎评测（无需 API Key）
  python -m evals.run_evals --llm                # 使用 LLM 评测（需要 GOOGLE_API_KEY）
  python -m evals.run_evals --langfuse           # 评测结果上报到 Langfuse

输出：
  - 控制台：每条 case 的结果 + 汇总指标
  - evals/results_<timestamp>.json：完整评测报告
  - （可选）Langfuse Score API：为每个 trace 打分
"""

from __future__ import annotations

import sys
import os
import json
import asyncio
import argparse
import time
from datetime import datetime
from pathlib import Path
from typing import Any

# 把 backend/ 加入 sys.path 以便 import app.*
sys.path.insert(0, str(Path(__file__).parent.parent))

# 设置环境变量（测试时默认使用规则引擎，无需 API Key）
os.environ.setdefault("TESTING", "1")

from app.agent.nodes.classifier import _rule_classify, _llm_classify


GOLDEN_PATH = Path(__file__).parent / "golden_dataset.json"
RESULTS_DIR = Path(__file__).parent


# ── 评测指标 ──────────────────────────────────────────────────────────────────

def _normalize_order_id(raw: str | None) -> str:
    """标准化订单号（去除空格、统一大写）"""
    if not raw:
        return ""
    return raw.strip().upper()


def _reason_matches(predicted: str | None, expected: str | None) -> bool:
    """
    宽松匹配退款原因：
    - expected=null 表示不检查（refund 以外的意图不要求精确 reason）
    - 规则引擎使用英文 key（damaged），golden_dataset 中 expected_reason 为中文描述
    """
    if expected is None:
        return True  # 不要求 reason 时直接通过

    # 中文 → 英文 key 映射（golden dataset 用中文描述，分类器输出英文 key）
    _REASON_MAP = {
        "商品破损": "damaged",
        "质量问题": "quality_issue",
        "发错商品": "wrong_item",
        "未收到商品": "not_received",
        "七天无理由": "other",  # 无理由退款时 reason 通常为 other
    }
    expected_key = _REASON_MAP.get(expected, expected)
    return (predicted or "other") == expected_key


def evaluate_case(case: dict, result: dict) -> dict:
    """
    评估单个 case 的预测结果，返回 {pass, errors, details}
    """
    errors = []

    # 1. Intent 精确匹配
    intent_ok = result.get("intent") == case["expected_intent"]
    if not intent_ok:
        errors.append(
            f"intent: expected={case['expected_intent']} got={result.get('intent')}"
        )

    # 2. Order ID 匹配（expected=null 时不检查）
    order_id_ok = True
    if case["expected_order_id"] is not None:
        expected_oid = _normalize_order_id(case["expected_order_id"])
        predicted_oid = _normalize_order_id(result.get("order_id"))
        order_id_ok = predicted_oid == expected_oid
        if not order_id_ok:
            errors.append(
                f"order_id: expected={expected_oid} got={predicted_oid}"
            )

    # 3. Reason 宽松匹配
    reason_ok = _reason_matches(result.get("reason"), case.get("expected_reason"))
    if not reason_ok:
        errors.append(
            f"reason: expected={case['expected_reason']} got={result.get('reason')}"
        )

    return {
        "pass": len(errors) == 0,
        "intent_ok": intent_ok,
        "order_id_ok": order_id_ok,
        "reason_ok": reason_ok,
        "errors": errors,
    }


# ── 分类器调用 ─────────────────────────────────────────────────────────────────

async def classify_one(text: str, use_llm: bool) -> dict:
    """调用分类器（LLM 或规则引擎）返回原始结果"""
    if use_llm:
        try:
            return await _llm_classify(text)
        except Exception as e:
            # LLM 失败自动降级为规则引擎
            print(f"  [WARN] LLM failed ({e}), falling back to rules", flush=True)
            return _rule_classify(text)
    else:
        return _rule_classify(text)


# ── Langfuse 上报 ──────────────────────────────────────────────────────────────

def _report_to_langfuse(run_id: str, case_results: list[dict]) -> None:
    """
    将 evals 结果上报到 Langfuse Score API。
    需要 LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY 环境变量。
    """
    try:
        from langfuse import Langfuse
    except ImportError:
        print("[WARN] langfuse 未安装，跳过上报")
        return

    try:
        lf = Langfuse()
        # 为每个 case 创建一个 evaluation trace
        for cr in case_results:
            trace = lf.trace(
                name=f"eval_{cr['case_id']}",
                metadata={
                    "run_id": run_id,
                    "input": cr["input"],
                    "expected_intent": cr["expected_intent"],
                    "predicted_intent": cr["predicted_intent"],
                    "tags": cr.get("tags", []),
                },
            )
            # 打分：1.0 全部通过，0.0 全部失败
            lf.score(
                trace_id=trace.id,
                name="eval_pass",
                value=1.0 if cr["pass"] else 0.0,
                comment="; ".join(cr["errors"]) if cr["errors"] else "OK",
            )
            # 单独记录 intent accuracy 分数
            lf.score(
                trace_id=trace.id,
                name="intent_accuracy",
                value=1.0 if cr["intent_ok"] else 0.0,
            )
        lf.flush()
        print(f"[INFO] Langfuse: {len(case_results)} scores uploaded (run_id={run_id})")
    except Exception as e:
        print(f"[WARN] Langfuse upload failed: {e}")


# ── 主评测逻辑 ─────────────────────────────────────────────────────────────────

async def run_evals(use_llm: bool = False, report_langfuse: bool = False) -> dict:
    """
    执行完整评测流程，返回汇总报告 dict。
    """
    dataset = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    cases = dataset["cases"]

    run_id = f"eval_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    mode = "llm" if use_llm else "rules"
    print(f"\n{'='*60}")
    print(f"  Enterprise Ticket Agent — Classifier Evals")
    print(f"  Run ID : {run_id}")
    print(f"  Mode   : {mode}")
    print(f"  Cases  : {len(cases)}")
    print(f"{'='*60}\n")

    case_results: list[dict] = []
    total = len(cases)
    pass_count = 0
    intent_correct = 0
    order_id_correct = 0
    reason_correct = 0
    order_id_total = 0

    for case in cases:
        t0 = time.perf_counter()
        result = await classify_one(case["input"], use_llm)
        latency_ms = round((time.perf_counter() - t0) * 1000, 1)

        eval_result = evaluate_case(case, result)
        passed = eval_result["pass"]

        status = "PASS" if passed else "FAIL"
        pass_count += 1 if passed else 0
        intent_correct += 1 if eval_result["intent_ok"] else 0
        reason_correct += 1 if eval_result["reason_ok"] else 0

        if case["expected_order_id"] is not None:
            order_id_total += 1
            order_id_correct += 1 if eval_result["order_id_ok"] else 0

        print(f"  [{status}] {case['id']} | {case['input'][:40]:<40} | {latency_ms:>6.1f}ms")
        if not passed:
            for err in eval_result["errors"]:
                print(f"          ↳ {err}")

        case_results.append({
            "case_id": case["id"],
            "input": case["input"],
            "tags": case.get("tags", []),
            "expected_intent": case["expected_intent"],
            "predicted_intent": result.get("intent"),
            "expected_order_id": case.get("expected_order_id"),
            "predicted_order_id": result.get("order_id"),
            "expected_reason": case.get("expected_reason"),
            "predicted_reason": result.get("reason"),
            "pass": passed,
            "intent_ok": eval_result["intent_ok"],
            "order_id_ok": eval_result["order_id_ok"],
            "reason_ok": eval_result["reason_ok"],
            "errors": eval_result["errors"],
            "latency_ms": latency_ms,
            "method": result.get("_method", mode),
        })

    # ── 汇总指标 ──
    intent_acc = intent_correct / total
    order_id_acc = order_id_correct / order_id_total if order_id_total else 1.0
    reason_acc = reason_correct / total
    overall_pass = pass_count / total

    # 按 tag 细分 intent 准确率
    tag_stats: dict[str, dict] = {}
    for cr in case_results:
        for tag in cr["tags"]:
            if tag not in tag_stats:
                tag_stats[tag] = {"total": 0, "intent_ok": 0}
            tag_stats[tag]["total"] += 1
            tag_stats[tag]["intent_ok"] += 1 if cr["intent_ok"] else 0

    print(f"\n{'─'*60}")
    print(f"  Results Summary")
    print(f"{'─'*60}")
    print(f"  Overall Pass Rate  : {overall_pass:.1%} ({pass_count}/{total})")
    print(f"  Intent Accuracy    : {intent_acc:.1%} ({intent_correct}/{total})")
    print(f"  Order-ID Accuracy  : {order_id_acc:.1%} ({order_id_correct}/{order_id_total})")
    print(f"  Reason Accuracy    : {reason_acc:.1%} ({reason_correct}/{total})")
    print(f"  Avg Latency        : {sum(c['latency_ms'] for c in case_results)/total:.1f}ms")

    print(f"\n  Intent Accuracy by Tag:")
    for tag, stat in sorted(tag_stats.items()):
        acc = stat["intent_ok"] / stat["total"]
        print(f"    {tag:<30} {acc:.1%} ({stat['intent_ok']}/{stat['total']})")

    # ── 保存报告 ──
    report = {
        "run_id": run_id,
        "mode": mode,
        "timestamp": datetime.now().isoformat(),
        "metrics": {
            "overall_pass_rate": round(overall_pass, 4),
            "intent_accuracy": round(intent_acc, 4),
            "order_id_accuracy": round(order_id_acc, 4),
            "reason_accuracy": round(reason_acc, 4),
            "avg_latency_ms": round(sum(c["latency_ms"] for c in case_results) / total, 1),
        },
        "tag_stats": {
            tag: {"accuracy": round(s["intent_ok"] / s["total"], 4), **s}
            for tag, s in tag_stats.items()
        },
        "cases": case_results,
    }

    report_path = RESULTS_DIR / f"results_{run_id}.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  Report saved: {report_path}")

    # ── Langfuse 上报 ──
    if report_langfuse:
        _report_to_langfuse(run_id, case_results)

    # ── 质量门控：intent 准确率 < 75% 时退出码非零（供 CI 使用）──
    threshold = 0.75
    if intent_acc < threshold:
        print(f"\n  [ERROR] Intent accuracy {intent_acc:.1%} below threshold {threshold:.0%}")
        sys.exit(1)

    print(f"\n  [OK] All checks passed.\n")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Run classifier evals")
    parser.add_argument("--llm", action="store_true", help="Use LLM classifier (requires GOOGLE_API_KEY)")
    parser.add_argument("--langfuse", action="store_true", help="Upload scores to Langfuse")
    args = parser.parse_args()
    asyncio.run(run_evals(use_llm=args.llm, report_langfuse=args.langfuse))


if __name__ == "__main__":
    main()
