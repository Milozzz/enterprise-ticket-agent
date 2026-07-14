"""Policy-as-Code helpers for tool access and refund governance."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from app.core.config import get_settings


DEFAULT_POLICY_PATH = Path(__file__).resolve().parents[1] / "policies" / "agent_policy.json"


@dataclass(frozen=True)
class PolicyDecision:
    policy_version: str
    effect: str
    allowed: bool = True
    requires_human_review: bool = False
    matched_rules: tuple[str, ...] = ()
    reason: str = ""
    policy_variant: str = "stable"
    rollout_bucket: int = 0

    def to_audit_event(self) -> dict[str, Any]:
        return {
            "policy_version": self.policy_version,
            "effect": self.effect,
            "allowed": self.allowed,
            "requires_human_review": self.requires_human_review,
            "matched_rules": list(self.matched_rules),
            "reason": self.reason,
            "policy_variant": self.policy_variant,
            "rollout_bucket": self.rollout_bucket,
        }


@lru_cache(maxsize=1)
def load_policy() -> dict[str, Any]:
    with DEFAULT_POLICY_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def get_policy_version() -> str:
    return str(load_policy().get("version", "unknown"))


def select_policy(routing_key: str = "global") -> tuple[dict[str, Any], str, int]:
    stable = load_policy()
    settings = get_settings()
    bucket = int(hashlib.sha256(str(routing_key or "global").encode()).hexdigest()[:8], 16) % 100
    percent = min(max(settings.policy_canary_percent, 0), 100)
    if not settings.policy_canary_json.strip() or bucket >= percent:
        return stable, "stable", bucket
    try:
        candidate = json.loads(settings.policy_canary_json)
        if not isinstance(candidate, dict) or not candidate.get("version"):
            return stable, "stable", bucket
        return candidate, "canary", bucket
    except (TypeError, ValueError):
        return stable, "stable", bucket


def evaluate_action_policy(
    role: str,
    action: str,
    attributes: Mapping[str, Any] | None = None,
    routing_key: str = "global",
) -> PolicyDecision:
    policy, variant, bucket = select_policy(routing_key)
    action_policy = policy.get("actions", {}).get(action)
    version = str(policy.get("version", "unknown"))
    normalized_role = str(role or "").upper()

    if action_policy is None:
        default_effect = str(policy.get("default_action_effect", "deny")).lower()
        allowed = default_effect == "allow"
        return PolicyDecision(
            policy_version=version,
            effect="allow" if allowed else "deny",
            allowed=allowed,
            matched_rules=("default_action_effect",),
            reason=f"Action '{action}' uses default policy effect '{default_effect}'.",
            policy_variant=variant,
            rollout_bucket=bucket,
        )

    allowed_roles = {str(item).upper() for item in action_policy.get("allowed_roles", [])}
    allowed = normalized_role in allowed_roles
    effect = "allow" if allowed else "deny"
    reason = (
        f"Role '{normalized_role}' is allowed to execute '{action}'."
        if allowed
        else f"Role '{normalized_role}' is not allowed to execute '{action}'."
    )
    return PolicyDecision(
        policy_version=version,
        effect=effect,
        allowed=allowed,
        matched_rules=(f"actions.{action}.allowed_roles",),
        reason=reason,
        policy_variant=variant,
        rollout_bucket=bucket,
    )


def allowed_roles_for_action(action: str, routing_key: str = "global") -> set[str] | None:
    action_policy = select_policy(routing_key)[0].get("actions", {}).get(action)
    if action_policy is None:
        return None
    return {str(item).upper() for item in action_policy.get("allowed_roles", [])}


def evaluate_refund_review_policy(
    *,
    amount: float,
    risk_score: int,
    risk_level: str,
    user_history: Mapping[str, Any] | None = None,
    routing_key: str = "global",
) -> PolicyDecision:
    policy, variant, bucket = select_policy(routing_key)
    return evaluate_refund_review_policy_document(
        policy,
        amount=amount,
        risk_score=risk_score,
        risk_level=risk_level,
        user_history=user_history,
        policy_variant=variant,
        rollout_bucket=bucket,
    )


def evaluate_refund_review_policy_document(
    policy: Mapping[str, Any],
    *,
    amount: float,
    risk_score: int,
    risk_level: str,
    user_history: Mapping[str, Any] | None = None,
    policy_variant: str = "counterfactual",
    rollout_bucket: int = 0,
) -> PolicyDecision:
    """Evaluate an explicit policy document without changing live rollout state."""

    version = str(policy.get("version", "unknown"))
    attributes = {
        "amount": float(amount or 0),
        "risk_score": int(risk_score or 0),
        "risk_level": str(risk_level or "").lower(),
        "user_history": dict(user_history or {}),
    }

    matched: list[str] = []
    for rule in policy.get("refund_review", {}).get("rules", []):
        if _matches_refund_rule(rule.get("when", {}), attributes):
            matched.append(str(rule.get("id", "unknown_rule")))

    if matched:
        return PolicyDecision(
            policy_version=version,
            effect="require_human_review",
            allowed=True,
            requires_human_review=True,
            matched_rules=tuple(matched),
            reason="Refund review policy requires HITL.",
            policy_variant=policy_variant,
            rollout_bucket=rollout_bucket,
        )

    return PolicyDecision(
        policy_version=version,
        effect="allow_auto_approval",
        allowed=True,
        requires_human_review=False,
        matched_rules=(),
        reason="No refund review policy rule matched.",
        policy_variant=policy_variant,
        rollout_bucket=rollout_bucket,
    )


def evaluate_permission_request_policy(
    *,
    system: str,
    permission_level: str,
    routing_key: str = "global",
) -> PolicyDecision:
    policy, variant, bucket = select_policy(routing_key)
    return evaluate_permission_request_policy_document(
        policy,
        system=system,
        permission_level=permission_level,
        policy_variant=variant,
        rollout_bucket=bucket,
    )


def evaluate_permission_request_policy_document(
    policy: Mapping[str, Any],
    *,
    system: str,
    permission_level: str,
    policy_variant: str = "counterfactual",
    rollout_bucket: int = 0,
) -> PolicyDecision:
    version = str(policy.get("version", "unknown"))
    attributes = {
        "system": _normalize_text(system),
        "permission_level": _normalize_text(permission_level),
    }

    matched: list[str] = []
    for rule in policy.get("permission_request_review", {}).get("rules", []):
        if _matches_permission_rule(rule.get("when", {}), attributes):
            matched.append(str(rule.get("id", "unknown_rule")))

    if matched:
        return PolicyDecision(
            policy_version=version,
            effect="require_human_review",
            allowed=True,
            requires_human_review=True,
            matched_rules=tuple(matched),
            reason="Permission request policy requires HITL.",
            policy_variant=policy_variant,
            rollout_bucket=rollout_bucket,
        )

    return PolicyDecision(
        policy_version=version,
        effect="allow_auto_approval",
        allowed=True,
        requires_human_review=False,
        matched_rules=(),
        reason="No permission request policy rule matched.",
        policy_variant=policy_variant,
        rollout_bucket=rollout_bucket,
    )


def evaluate_reimbursement_policy(
    *,
    amount: float,
    category: str,
    routing_key: str = "global",
) -> PolicyDecision:
    policy, variant, bucket = select_policy(routing_key)
    return evaluate_reimbursement_policy_document(
        policy,
        amount=amount,
        category=category,
        policy_variant=variant,
        rollout_bucket=bucket,
    )


def evaluate_reimbursement_policy_document(
    policy: Mapping[str, Any],
    *,
    amount: float,
    category: str,
    policy_variant: str = "counterfactual",
    rollout_bucket: int = 0,
) -> PolicyDecision:
    version = str(policy.get("version", "unknown"))
    attributes = {
        "amount": float(amount or 0),
        "category": _normalize_text(category),
    }

    matched: list[str] = []
    for rule in policy.get("reimbursement_review", {}).get("rules", []):
        if _matches_reimbursement_rule(rule.get("when", {}), attributes):
            matched.append(str(rule.get("id", "unknown_rule")))

    if matched:
        return PolicyDecision(
            policy_version=version,
            effect="require_human_review",
            allowed=True,
            requires_human_review=True,
            matched_rules=tuple(matched),
            reason="Reimbursement policy requires HITL.",
            policy_variant=policy_variant,
            rollout_bucket=rollout_bucket,
        )

    return PolicyDecision(
        policy_version=version,
        effect="allow_auto_approval",
        allowed=True,
        requires_human_review=False,
        matched_rules=(),
        reason="No reimbursement policy rule matched.",
        policy_variant=policy_variant,
        rollout_bucket=rollout_bucket,
    )


def _matches_refund_rule(when: Mapping[str, Any], attributes: Mapping[str, Any]) -> bool:
    for key, expected in when.items():
        if key == "amount_gt":
            if not (attributes["amount"] > float(expected)):
                return False
        elif key == "amount_gte":
            if not (attributes["amount"] >= float(expected)):
                return False
        elif key == "risk_score_gte":
            if not (attributes["risk_score"] >= int(expected)):
                return False
        elif key == "risk_level_in":
            expected_levels = {str(item).lower() for item in expected}
            if attributes["risk_level"] not in expected_levels:
                return False
        elif key == "user_history_fraud_flag":
            fraud_flag = bool(attributes["user_history"].get("fraud_flag"))
            if fraud_flag is not bool(expected):
                return False
        else:
            return False
    return True


def _matches_permission_rule(when: Mapping[str, Any], attributes: Mapping[str, Any]) -> bool:
    for key, expected in when.items():
        if key == "permission_level_in":
            if not _value_contains_any(attributes["permission_level"], expected):
                return False
        elif key == "system_in":
            if not _value_contains_any(attributes["system"], expected):
                return False
        else:
            return False
    return True


def _matches_reimbursement_rule(when: Mapping[str, Any], attributes: Mapping[str, Any]) -> bool:
    for key, expected in when.items():
        if key == "amount_gt":
            if not (attributes["amount"] > float(expected)):
                return False
        elif key == "amount_gte":
            if not (attributes["amount"] >= float(expected)):
                return False
        elif key == "category_in":
            if not _value_contains_any(attributes["category"], expected):
                return False
        else:
            return False
    return True


def _normalize_text(value: str) -> str:
    return str(value or "").strip().lower()


def _value_contains_any(value: str, expected: Any) -> bool:
    normalized = _normalize_text(value)
    return any(_normalize_text(item) in normalized for item in expected)
