"""Per-task LLM budget context shared by Agent runtimes and the LLM gateway."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from decimal import Decimal
from typing import Iterator


@dataclass(frozen=True)
class LLMTaskBudget:
    max_calls: int
    max_cost_usd: Decimal


_ACTIVE_TASK_BUDGET: ContextVar[LLMTaskBudget | None] = ContextVar(
    "active_llm_task_budget",
    default=None,
)


def active_task_budget() -> LLMTaskBudget | None:
    return _ACTIVE_TASK_BUDGET.get()


@contextmanager
def use_task_budget(*, max_calls: int, max_cost_usd: float | str) -> Iterator[None]:
    token = _ACTIVE_TASK_BUDGET.set(
        LLMTaskBudget(
            max_calls=max(0, int(max_calls)),
            max_cost_usd=max(Decimal("0"), Decimal(str(max_cost_usd))),
        )
    )
    try:
        yield
    finally:
        _ACTIVE_TASK_BUDGET.reset(token)
