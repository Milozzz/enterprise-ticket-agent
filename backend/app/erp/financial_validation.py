"""Deterministic financial invariants applied before ERP posting."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Mapping


class FinancialInvariantError(ValueError):
    pass


def as_money(value: object) -> Decimal:
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise FinancialInvariantError(f"invalid monetary value: {value!r}") from exc


def require_non_negative(amounts: Mapping[str, object]) -> None:
    invalid = [name for name, value in amounts.items() if as_money(value) < Decimal("0.00")]
    if invalid:
        raise FinancialInvariantError(f"negative amounts are forbidden: {sorted(invalid)}")


def require_balanced_journal(total_debit: object, total_credit: object) -> None:
    debit = as_money(total_debit)
    credit = as_money(total_credit)
    if debit != credit:
        raise FinancialInvariantError(
            f"journal is not balanced: debit={debit} credit={credit}"
        )


def require_currency_consistency(currencies: Mapping[str, str | None]) -> str:
    normalized = {
        name: str(value or "").strip().upper()
        for name, value in currencies.items()
    }
    missing = sorted(name for name, value in normalized.items() if len(value) != 3)
    if missing:
        raise FinancialInvariantError(f"missing or invalid currency: {missing}")
    distinct = sorted(set(normalized.values()))
    if len(distinct) != 1:
        details = ", ".join(f"{name}={value}" for name, value in sorted(normalized.items()))
        raise FinancialInvariantError(f"currency mismatch: {details}")
    return distinct[0]
