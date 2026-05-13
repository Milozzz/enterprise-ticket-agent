"""
敏感数据脱敏工具

在写入审计日志或 SSE 输出前，对包含 PII 的字段进行脱敏。
原则：保留字段结构和部分可读性，隐藏核心敏感值。
"""

from __future__ import annotations
import re

# 需要脱敏的字段名（不区分大小写，支持 snake_case 和 camelCase）
_SENSITIVE_KEYS = {
    "email", "to_email", "toemail", "to", "notificationto",
    "phone", "mobile",
    "id_card", "idcard", "id_number",
    "bank_account", "bankaccount",
    "password", "token", "secret", "app_password",
}

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[a-zA-Z]{2,}")
_PHONE_RE = re.compile(r"1[3-9]\d{9}")
_SECRET_RE = re.compile(
    r"(?i)\b("
    r"AIza[0-9A-Za-z_-]{20,}|"
    r"sk-[0-9A-Za-z_-]{12,}|"
    r"ghp_[0-9A-Za-z]{20,}|"
    r"xox[baprs]-[0-9A-Za-z-]{12,}"
    r")\b"
)


def _mask_email(value: str) -> str:
    """alice@example.com → al***@example.com"""
    m = _EMAIL_RE.search(value)
    if not m:
        return value
    local, domain = m.group().split("@", 1)
    masked = local[:2] + "***@" + domain
    return _EMAIL_RE.sub(masked, value)


def _mask_phone(value: str) -> str:
    """13800138000 → 138****8000"""
    return _PHONE_RE.sub(lambda m: m.group()[:3] + "****" + m.group()[-4:], value)


def _mask_secret(value: str) -> str:
    return _SECRET_RE.sub(lambda m: m.group()[:4] + "***" + m.group()[-4:], value)


def _mask_value(key: str, value: object) -> object:
    """对单个值进行脱敏"""
    if not isinstance(value, str):
        return value
    lower_key = key.lower().replace("_", "").replace("-", "")
    if lower_key in _SENSITIVE_KEYS:
        if "@" in value:
            return _mask_email(value)
        if len(value) >= 6:
            return value[:2] + "***" + value[-2:]
        return "***"
    # 值本身含敏感模式（即使字段名不敏感）
    value = _mask_email(value)
    value = _mask_phone(value)
    value = _mask_secret(value)
    return value


def mask_dict(data: dict | None, depth: int = 0) -> dict | None:
    """
    递归脱敏字典（最多递归 3 层，避免大型嵌套对象性能问题）。
    返回新字典，不修改原始对象。
    """
    if not data or depth > 3:
        return data
    result: dict = {}
    for k, v in data.items():
        if isinstance(v, dict):
            result[k] = mask_dict(v, depth + 1)
        elif isinstance(v, list):
            result[k] = [mask_dict(i, depth + 1) if isinstance(i, dict) else i for i in v]
        else:
            result[k] = _mask_value(k, v)
    return result
