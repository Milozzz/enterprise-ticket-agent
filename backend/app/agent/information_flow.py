"""Field-level provenance and taint checks for high-risk Agent actions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Sequence


class TrustLabel(str, Enum):
    SYSTEM_CONFIG = "system_config"
    TRUSTED_ENTERPRISE_DATA = "trusted_enterprise_data"
    USER_PROVIDED = "user_provided"
    RAG_UNTRUSTED = "rag_untrusted"
    EXTERNAL_TOOL_UNTRUSTED = "external_tool_untrusted"
    MODEL_GENERATED = "model_generated"
    POLICY_VERIFIED = "policy_verified"
    HUMAN_VERIFIED = "human_verified"
    UNKNOWN = "unknown"


TRUSTED_WRITE_LABELS = {
    TrustLabel.SYSTEM_CONFIG.value,
    TrustLabel.TRUSTED_ENTERPRISE_DATA.value,
    TrustLabel.POLICY_VERIFIED.value,
    TrustLabel.HUMAN_VERIFIED.value,
}


@dataclass(frozen=True)
class InformationFlowDecision:
    allowed: bool
    mode: str
    reason_code: str
    reason: str
    checked_fields: tuple[str, ...]
    violations: tuple[dict[str, Any], ...]

    def to_audit_event(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "mode": self.mode,
            "reason_code": self.reason_code,
            "reason": self.reason,
            "checked_fields": list(self.checked_fields),
            "violations": [dict(item) for item in self.violations],
        }


def validate_information_flow(
    *,
    side_effect: str,
    risk_level: str,
    args: Mapping[str, Any],
    provenance: Mapping[str, Any] | None,
    required_fields: Sequence[str],
    mode: str = "audit",
    dry_run: bool = False,
) -> InformationFlowDecision:
    normalized_mode = str(mode or "audit").lower()
    high_risk_write = side_effect in {"write", "external"} and risk_level in {"medium", "high"}
    if not high_risk_write:
        return InformationFlowDecision(True, normalized_mode, "FLOW_NOT_REQUIRED", "Information-flow enforcement is not required for this action.", (), ())

    provenance_map = dict(provenance or {})
    fields = tuple(dict.fromkeys([*required_fields, *[key for key in args if key in provenance_map]]))
    violations: list[dict[str, Any]] = []
    for field_name in fields:
        if field_name not in args:
            continue
        record = _provenance_record(provenance_map.get(field_name))
        label = record["label"]
        evidence_ids = record["evidence_ids"]
        verified_by = record["verified_by"]
        if label not in TRUSTED_WRITE_LABELS:
            violations.append({"field": field_name, "label": label, "reason": "untrusted_source"})
        elif label in {TrustLabel.POLICY_VERIFIED.value, TrustLabel.HUMAN_VERIFIED.value} and not evidence_ids:
            violations.append({"field": field_name, "label": label, "reason": "evidence_missing"})
        elif label == TrustLabel.POLICY_VERIFIED.value and not verified_by:
            violations.append({"field": field_name, "label": label, "reason": "verifier_missing"})

    should_block = bool(violations) and normalized_mode == "enforce" and not dry_run
    return InformationFlowDecision(
        allowed=not should_block,
        mode=normalized_mode,
        reason_code="TAINT_FLOW_BLOCKED" if should_block else "TAINT_FLOW_OBSERVED" if violations else "TAINT_FLOW_ALLOWED",
        reason=(
            "Untrusted or unverified data reached a high-risk tool input."
            if violations
            else "All high-risk tool inputs have trusted provenance."
        ),
        checked_fields=fields,
        violations=tuple(violations),
    )


def declassify_for_verified_execution(
    *,
    fields: Mapping[str, Any],
    cited_evidence_ids: Sequence[str],
    verifier: str,
    human_approved: bool,
) -> dict[str, dict[str, Any]]:
    label = TrustLabel.HUMAN_VERIFIED.value if human_approved else TrustLabel.POLICY_VERIFIED.value
    evidence_ids = [str(item) for item in cited_evidence_ids if item]
    return {
        field_name: {
            "label": label,
            "source": "hitl_approval" if human_approved else "execution_verifier",
            "evidence_ids": evidence_ids,
            "verified_by": verifier,
            "value_fingerprint": _fingerprint(value),
        }
        for field_name, value in fields.items()
        if value is not None and value != ""
    }


def provenance_for_tool_output(data: Any, *, source: str) -> dict[str, dict[str, Any]]:
    if not isinstance(data, Mapping):
        return {"$": {"label": TrustLabel.EXTERNAL_TOOL_UNTRUSTED.value, "source": source}}
    return {
        str(field_name): {
            "label": TrustLabel.EXTERNAL_TOOL_UNTRUSTED.value,
            "source": source,
            "evidence_ids": [],
            "verified_by": None,
        }
        for field_name in data
    }


def _provenance_record(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        return {"label": value, "evidence_ids": [], "verified_by": None}
    if isinstance(value, Mapping):
        return {
            "label": str(value.get("label") or TrustLabel.UNKNOWN.value),
            "evidence_ids": list(value.get("evidence_ids") or []),
            "verified_by": value.get("verified_by"),
        }
    return {"label": TrustLabel.UNKNOWN.value, "evidence_ids": [], "verified_by": None}


def _fingerprint(value: Any) -> str:
    import hashlib

    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:16]
