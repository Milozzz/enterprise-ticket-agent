"""Saga-style compensation primitives for side-effecting agent tools."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


SagaHandler = Callable[[dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True)
class SagaStep:
    name: str
    action: SagaHandler
    compensate: SagaHandler | None = None


@dataclass
class SagaExecution:
    saga_id: str
    completed_steps: list[str] = field(default_factory=list)
    compensated_steps: list[str] = field(default_factory=list)
    failed_step: str | None = None
    error: str | None = None
    outputs: dict[str, Any] = field(default_factory=dict)

    @property
    def success(self) -> bool:
        return self.failed_step is None

    def to_dict(self) -> dict[str, Any]:
        return {
            "saga_id": self.saga_id,
            "success": self.success,
            "completed_steps": self.completed_steps,
            "compensated_steps": self.compensated_steps,
            "failed_step": self.failed_step,
            "error": self.error,
            "outputs": self.outputs,
        }


def execute_saga(saga_id: str, steps: list[SagaStep], context: dict[str, Any]) -> SagaExecution:
    execution = SagaExecution(saga_id=saga_id)
    completed: list[SagaStep] = []
    try:
        for step in steps:
            execution.outputs[step.name] = step.action(context)
            execution.completed_steps.append(step.name)
            completed.append(step)
    except Exception as exc:
        execution.failed_step = step.name
        execution.error = str(exc)
        for completed_step in reversed(completed):
            if completed_step.compensate is None:
                continue
            try:
                execution.outputs[f"compensate:{completed_step.name}"] = completed_step.compensate(context)
                execution.compensated_steps.append(completed_step.name)
            except Exception as compensation_exc:
                execution.outputs[f"compensate_error:{completed_step.name}"] = str(compensation_exc)
    return execution


def refund_saga_template() -> dict[str, Any]:
    return {
        "saga_id": "refund_execution_saga",
        "steps": [
            {
                "name": "create_refund",
                "compensation": "cancel_refund_or_create_adjustment",
                "side_effect": "payment_ledger",
            },
            {
                "name": "send_notification",
                "compensation": "send_reversal_notification",
                "side_effect": "external_message",
            },
        ],
        "guarantee": "If a later side effect fails, completed compensatable steps are unwound in reverse order.",
    }
