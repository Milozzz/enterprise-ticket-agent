"""Deterministic guard signals for untrusted Agent input and evals."""

from __future__ import annotations

from dataclasses import dataclass
import re


_INJECTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("ignore_instructions", re.compile(r"ignore (all |the )?(previous|prior|system) instructions?", re.I)),
    ("system_prompt_exfiltration", re.compile(r"(show|reveal|print|leak).{0,30}(system prompt|developer message)", re.I)),
    ("secret_exfiltration", re.compile(r"(show|reveal|print|leak).{0,30}(api[_ -]?key|token|password|secret)", re.I)),
    ("policy_bypass", re.compile(r"(bypass|disable|skip|override).{0,30}(approval|policy|permission|rbac|guard)", re.I)),
    ("role_impersonation", re.compile(r"(act as|pretend|you are now).{0,30}(admin|manager|system|root)", re.I)),
    ("tool_coercion", re.compile(r"(call|execute|run).{0,30}(refund|payment|grant|delete).{0,20}(tool|api|command)?", re.I)),
    ("chinese_injection", re.compile(r"忽略.{0,12}(之前|以上|系统).{0,12}(指令|提示)|绕过.{0,12}(审批|权限|策略)")),
)


@dataclass(frozen=True)
class SafetyInspection:
    flagged: bool
    categories: tuple[str, ...]
    handling: str

    def to_dict(self) -> dict:
        return {
            "flagged": self.flagged,
            "categories": list(self.categories),
            "handling": self.handling,
        }


def inspect_untrusted_text(text: str) -> SafetyInspection:
    categories = tuple(name for name, pattern in _INJECTION_PATTERNS if pattern.search(text or ""))
    return SafetyInspection(
        flagged=bool(categories),
        categories=categories,
        handling="treat_as_untrusted_data" if categories else "normal_processing",
    )
