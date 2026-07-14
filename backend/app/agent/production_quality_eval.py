"""Deterministic production-like quality suite for core agent decisions.

The suite is generated from explicit business variations so it stays compact in
source control while still exercising 120 distinct routing, extraction, policy,
and authorization decisions.  It complements, rather than replaces, golden
trajectory and live shadow-traffic evaluation.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from app.agent.generic_runtime import extract_slots
from app.agent.scenario_registry import get_default_registry
from app.core.policy import (
    evaluate_action_policy,
    evaluate_permission_request_policy,
    evaluate_refund_review_policy,
    evaluate_reimbursement_policy,
)


@dataclass(frozen=True)
class QualityCase:
    case_id: str
    dimension: str
    payload: dict[str, Any]
    expected: Any


def build_production_quality_cases() -> list[QualityCase]:
    cases: list[QualityCase] = []

    route_prompts = {
        "refund": [
            f"订单号 {610000 + index} 申请退款，商品破损"
            for index in range(15)
        ],
        "permission_request": [
            f"申请 GitHub 管理员权限，用于第 {index + 1} 次生产发布"
            for index in range(15)
        ],
        "reimbursement": [
            f"报销 {120 + index * 20} 元差旅费，发票编号 INV-{index + 1:03d}"
            for index in range(15)
        ],
    }
    route_index = 1
    for scenario_id, prompts in route_prompts.items():
        for prompt in prompts:
            cases.append(
                QualityCase(
                    case_id=f"ROUTE-{route_index:03d}",
                    dimension="routing",
                    payload={"message": prompt},
                    expected=scenario_id,
                )
            )
            route_index += 1

    permission_runtime = get_default_registry().get("permission_request").runtime
    permission_variants = [
        ("GitHub", "管理员", "admin"),
        ("GitHub", "写入", "write"),
        ("GitHub", "只读", "read"),
        ("Jira", "管理员", "admin"),
        ("Jira", "审批", "approve"),
        ("Jira", "只读", "read"),
        ("CRM", "写入", "write"),
        ("CRM", "只读", "read"),
        ("Finance", "审批", "approve"),
        ("Finance", "只读", "read"),
        ("Production Database", "管理员", "admin"),
        ("Production Database", "写入", "write"),
        ("Production Database", "只读", "read"),
        ("GitHub", "审批", "approve"),
        ("CRM", "管理员", "admin"),
    ]
    for index, (system, phrase, level) in enumerate(permission_variants, start=1):
        cases.append(
            QualityCase(
                case_id=f"SLOT-P-{index:03d}",
                dimension="slot_extraction",
                payload={
                    "scenario_id": "permission_request",
                    "message": f"申请 {system} {phrase}权限，用于项目交付",
                    "slot_config": permission_runtime["slot_extraction"],
                },
                expected={"system": system, "permission_level": level},
            )
        )

    reimbursement_runtime = get_default_registry().get("reimbursement").runtime
    reimbursement_variants = [
        (88, "差旅", "travel"),
        (120, "出差", "travel"),
        (260, "住宿", "hotel"),
        (320, "酒店", "hotel"),
        (45, "打车", "taxi"),
        (66, "出租", "taxi"),
        (90, "餐费", "meal"),
        (130, "吃饭", "meal"),
        (180, "办公耗材", "office"),
        (240, "办公用品", "office"),
        (500, "客户招待", "entertainment"),
        (760, "接待客户", "entertainment"),
        (999, "差旅", "travel"),
        (1001, "住宿", "hotel"),
        (1500, "客户招待", "entertainment"),
    ]
    for index, (amount, phrase, category) in enumerate(reimbursement_variants, start=1):
        cases.append(
            QualityCase(
                case_id=f"SLOT-R-{index:03d}",
                dimension="slot_extraction",
                payload={
                    "scenario_id": "reimbursement",
                    "message": f"报销 {amount} 元{phrase}费用，已提供发票",
                    "slot_config": reimbursement_runtime["slot_extraction"],
                },
                expected={"amount": amount, "category": category},
            )
        )

    permission_policy_cases = [
        ("GitHub", "admin", True),
        ("GitHub", "write", True),
        ("GitHub", "approve", True),
        ("GitHub", "read", False),
        ("Finance", "read", True),
        ("CRM", "read", True),
        ("Production Database", "read", True),
        ("Jira", "read", False),
        ("Jira", "admin", True),
        ("Finance", "admin", True),
    ]
    for index, (system, level, expected) in enumerate(permission_policy_cases, start=1):
        cases.append(
            QualityCase(
                case_id=f"POLICY-P-{index:03d}",
                dimension="policy",
                payload={"kind": "permission", "system": system, "level": level},
                expected=expected,
            )
        )

    reimbursement_policy_cases = [
        (99, "meal", False),
        (999, "office", False),
        (1000, "office", False),
        (1001, "office", True),
        (5000, "meal", True),
        (100, "travel", True),
        (100, "hotel", True),
        (100, "entertainment", True),
        (900, "taxi", False),
        (2200, "travel", True),
    ]
    for index, (amount, category, expected) in enumerate(reimbursement_policy_cases, start=1):
        cases.append(
            QualityCase(
                case_id=f"POLICY-R-{index:03d}",
                dimension="policy",
                payload={"kind": "reimbursement", "amount": amount, "category": category},
                expected=expected,
            )
        )

    refund_policy_cases = [
        (100, 0, "low", False, False),
        (500, 0, "low", False, False),
        (501, 0, "low", False, True),
        (2000, 0, "low", False, True),
        (100, 39, "low", False, False),
        (100, 40, "medium", False, True),
        (100, 0, "high", False, True),
        (100, 0, "low", True, True),
        (800, 80, "high", True, True),
        (499, 10, "medium", False, False),
    ]
    for index, (amount, score, level, fraud, expected) in enumerate(refund_policy_cases, start=1):
        cases.append(
            QualityCase(
                case_id=f"POLICY-F-{index:03d}",
                dimension="policy",
                payload={
                    "kind": "refund",
                    "amount": amount,
                    "risk_score": score,
                    "risk_level": level,
                    "fraud": fraud,
                },
                expected=expected,
            )
        )

    authorization_cases = [
        ("USER", "lookup_order", True),
        ("USER", "check_risk_level", True),
        ("USER", "execute_refund", False),
        ("AGENT", "execute_refund", True),
        ("MANAGER", "approve_refund", True),
        ("USER", "approve_refund", False),
        ("USER", "create_permission_request", False),
        ("AGENT", "create_permission_request", True),
        ("SECURITY", "approve_permission_request", True),
        ("AGENT", "approve_permission_request", False),
        ("FINANCE", "approve_reimbursement", True),
        ("USER", "approve_reimbursement", False),
        ("AUDITOR", "erp_get_order", True),
        ("USER", "erp_create_credit_memo", False),
        ("USER", "undefined_privileged_action", False),
    ]
    for index, (role, action, expected) in enumerate(authorization_cases, start=1):
        cases.append(
            QualityCase(
                case_id=f"AUTH-{index:03d}",
                dimension="authorization",
                payload={"role": role, "action": action},
                expected=expected,
            )
        )

    return cases


def run_production_quality_eval() -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    dimension_totals: dict[str, list[bool]] = defaultdict(list)
    for case in build_production_quality_cases():
        actual = _evaluate_case(case)
        passed = actual == case.expected
        dimension_totals[case.dimension].append(passed)
        results.append(
            {
                "case_id": case.case_id,
                "dimension": case.dimension,
                "passed": passed,
                "expected": case.expected,
                "actual": actual,
            }
        )

    passed_count = sum(1 for item in results if item["passed"])
    return {
        "suite": "production-like-core-decisions-v1",
        "case_count": len(results),
        "passed_count": passed_count,
        "failed_count": len(results) - passed_count,
        "pass_rate": round(passed_count / len(results), 4) if results else 0.0,
        "dimensions": {
            name: {
                "case_count": len(values),
                "pass_rate": round(sum(values) / len(values), 4),
            }
            for name, values in sorted(dimension_totals.items())
        },
        "failures": [item for item in results if not item["passed"]],
    }


def _evaluate_case(case: QualityCase) -> Any:
    payload = case.payload
    if case.dimension == "routing":
        return get_default_registry().match(payload["message"]).scenario_id
    if case.dimension == "slot_extraction":
        slots = extract_slots(payload["message"], payload["slot_config"])
        return {key: slots.get(key) for key in case.expected}
    if case.dimension == "authorization":
        return evaluate_action_policy(payload["role"], payload["action"]).allowed
    if payload["kind"] == "permission":
        return evaluate_permission_request_policy(
            system=payload["system"], permission_level=payload["level"]
        ).requires_human_review
    if payload["kind"] == "reimbursement":
        return evaluate_reimbursement_policy(
            amount=payload["amount"], category=payload["category"]
        ).requires_human_review
    return evaluate_refund_review_policy(
        amount=payload["amount"],
        risk_score=payload["risk_score"],
        risk_level=payload["risk_level"],
        user_history={"fraud_flag": payload["fraud"]},
    ).requires_human_review
