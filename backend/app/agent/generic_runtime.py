"""Config-driven runtime for simple supervisor scenarios.

This runtime intentionally supports a small set of deterministic building
blocks first: slot extraction, one Tool Gateway call, one policy binding,
standard business UI events, and a final response template. That is enough to
move permission and reimbursement flows away from scenario-specific node code.
"""

from __future__ import annotations

import re
from typing import Any, Callable, Mapping

from app.agent.scenario_registry import ScenarioConfig, get_default_registry
from app.agent.state import AgentState
from app.agent.tool_gateway import execute_tool, gateway_context_from_state
from app.agent.tools.enterprise_tools import create_permission_request, create_reimbursement_request
from app.agent.ui_events import build_business_request_card, build_generic_approval_panel
from app.agent.utils import get_state_val
from app.core.logging import get_logger
from app.core.policy import (
    PolicyDecision,
    evaluate_permission_request_policy,
    evaluate_reimbursement_policy,
)

logger = get_logger(__name__)

TOOL_HANDLERS: dict[str, Any] = {
    "create_permission_request": create_permission_request,
    "create_reimbursement_request": create_reimbursement_request,
}

POLICY_HANDLERS: dict[str, Callable[..., PolicyDecision]] = {
    "permission_request_review": evaluate_permission_request_policy,
    "reimbursement_review": evaluate_reimbursement_policy,
}


def list_runtime_tool_names() -> set[str]:
    return set(TOOL_HANDLERS)


def list_runtime_policy_names() -> set[str]:
    return set(POLICY_HANDLERS)


def latest_human_message(state: AgentState) -> str:
    messages = get_state_val(state, "messages", [])
    for msg in reversed(messages):
        if hasattr(msg, "type") and msg.type == "human":
            return str(msg.content or "")
        if isinstance(msg, dict) and msg.get("role") == "user":
            return str(msg.get("content") or "")
    return ""


async def run_configured_scenario(
    state: AgentState,
    scenario_id: str,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    scenario = get_default_registry().get(scenario_id)
    runtime = scenario.runtime
    if not runtime:
        raise ValueError(f"Scenario '{scenario_id}' does not define runtime configuration.")

    message = latest_human_message(state)
    requester_id = str(get_state_val(state, "user_id", "anonymous") or "anonymous")
    thread_id = get_state_val(state, "thread_id")
    slots = extract_slots(message, runtime.get("slot_extraction", {}))

    base_context = {
        "input": {"message": message},
        "slots": slots,
        "context": {"user_id": requester_id, "thread_id": thread_id},
        "scenario": scenario.to_dict(),
    }

    policy_decision = evaluate_runtime_policy(runtime.get("policy", {}), base_context)
    tool_result = execute_runtime_tool(
        state=state,
        scenario=scenario,
        runtime=runtime,
        context=base_context,
        dry_run=dry_run,
    )
    if not tool_result.success:
        return {
            "intent": scenario.id,
            "scenario_id": scenario.id,
            "error_message": tool_result.error or f"{scenario.name} tool execution failed.",
            "tool_gateway_events": [tool_result.audit_event],
            "current_step": f"{scenario.id}_error",
        }

    request_data = dict(tool_result.data or {})
    request_data["requestId"] = request_data.get("requestId") or f"DRY_RUN_{scenario.id.upper()}"
    request_data["approvalRequired"] = policy_decision.requires_human_review
    request_data["policyDecision"] = policy_decision.to_audit_event()
    request_data["status"] = "pending_review" if policy_decision.requires_human_review else "auto_approved"

    full_context = {
        **base_context,
        "request": request_data,
        "policy": policy_decision.to_audit_event(),
        "approval_line": approval_line(runtime, policy_decision),
    }
    reply_text = render_template(str(runtime.get("reply_template", "")), full_context)
    ui_events = build_runtime_ui_events(
        scenario=scenario,
        runtime=runtime,
        context=full_context,
        requester_id=requester_id,
        thread_id=thread_id,
        policy_decision=policy_decision,
    )

    result: dict[str, Any] = {
        "intent": scenario.id,
        "scenario_id": scenario.id,
        "business_request": request_data,
        "approval_required": policy_decision.requires_human_review,
        "approval_type": str(runtime.get("approval_type") or scenario.id),
        "policy_events": [policy_decision.to_audit_event()],
        "tool_gateway_events": [tool_result.audit_event],
        "dry_run": dry_run,
        "reply_text": reply_text,
        "current_step": str(runtime.get("current_step") or f"{scenario.id}_done"),
        "ui_events": ui_events,
    }
    for key, expression in dict(runtime.get("state_outputs") or {}).items():
        result[key] = resolve_value(expression, full_context)
    return result


def extract_slots(message: str, config: Mapping[str, Any]) -> dict[str, Any]:
    fields = dict(config.get("fields") or {})
    return {field_name: extract_field(message, field_config) for field_name, field_config in fields.items()}


def extract_field(message: str, field_config: Mapping[str, Any]) -> Any:
    strategy = str(field_config.get("type") or "message_excerpt")
    text = message or ""
    lowered = text.lower()

    if strategy == "keyword_map":
        for pattern in field_config.get("patterns", []):
            keywords = [str(item).lower() for item in pattern.get("keywords", [])]
            if any(keyword and keyword in lowered for keyword in keywords):
                return pattern.get("value")
        fallback_regex = field_config.get("fallback_regex")
        if fallback_regex:
            match = re.search(str(fallback_regex), text, re.IGNORECASE)
            if match:
                return match.group(1)
        return field_config.get("default")

    if strategy == "keyword_enum":
        for option in field_config.get("options", []):
            keywords = [str(item).lower() for item in option.get("keywords", [])]
            if any(keyword and keyword in lowered for keyword in keywords):
                return option.get("value")
        return field_config.get("default")

    if strategy == "amount":
        regex = str(field_config.get("regex") or r"(?:¥|￥)?\s*(\d+(?:\.\d+)?)\s*(?:元|块|rmb|cny)?")
        match = re.search(regex, text, re.IGNORECASE)
        return float(match.group(1)) if match else float(field_config.get("default", 0))

    if strategy == "message_excerpt":
        max_length = int(field_config.get("max_length") or 500)
        return text[:max_length] or field_config.get("default") or ""

    return field_config.get("default")


def evaluate_runtime_policy(config: Mapping[str, Any], context: Mapping[str, Any]) -> PolicyDecision:
    policy_name = str(config.get("name") or "")
    handler = POLICY_HANDLERS.get(policy_name)
    if handler is None:
        raise ValueError(f"Unknown runtime policy '{policy_name}'.")
    args = {key: resolve_value(value, context) for key, value in dict(config.get("args") or {}).items()}
    return handler(**args)


def execute_runtime_tool(
    *,
    state: AgentState,
    scenario: ScenarioConfig,
    runtime: Mapping[str, Any],
    context: Mapping[str, Any],
    dry_run: bool = False,
):
    tool_config = dict(runtime.get("tool") or {})
    tool_name = str(tool_config.get("name") or "")
    handler = TOOL_HANDLERS.get(tool_name)
    if handler is None:
        raise ValueError(f"Unknown runtime tool '{tool_name}'.")
    args = {key: resolve_value(value, context) for key, value in dict(tool_config.get("args") or {}).items()}
    return execute_tool(
        tool_name,
        args,
        context=gateway_context_from_state(state, actor_role="AGENT", scenario=scenario.id, dry_run=dry_run),
        handler=handler,
    )


def build_runtime_ui_events(
    *,
    scenario: ScenarioConfig,
    runtime: Mapping[str, Any],
    context: Mapping[str, Any],
    requester_id: str,
    thread_id: str | None,
    policy_decision: PolicyDecision,
) -> list[dict[str, Any]]:
    ui_config = dict(runtime.get("ui") or {})
    events: list[dict[str, Any]] = []

    thinking = dict(ui_config.get("thinking_stream") or {})
    if thinking:
        events.append(
            {
                "type": "thinking_stream",
                "data": {
                    "steps": [
                        {
                            "step": str(thinking.get("step") or f"{scenario.id}_created"),
                            "label": render_any(thinking.get("label") or scenario.name, context),
                            "status": "done",
                            "detail": render_any(thinking.get("detail") or "", context),
                        }
                    ]
                },
            }
        )

    card = dict(ui_config.get("business_request_card") or {})
    if card:
        request_id = str(resolve_path("request.requestId", context) or "")
        events.append(
            build_business_request_card(
                scenario_id=scenario.id,
                scenario_name=scenario.name,
                request_id=request_id,
                status=str(resolve_path("request.status", context) or ""),
                title=render_any(card.get("title") or scenario.name, context),
                summary=render_any(card.get("summary") or scenario.description, context),
                fields=[
                    {
                        "label": render_any(field.get("label") or "", context),
                        "value": format_display_value(resolve_value(field.get("value"), context), field),
                        **({"tone": field["tone"]} if field.get("tone") else {}),
                    }
                    for field in card.get("fields", [])
                ],
                policy_decision=policy_decision,
                timeline=[
                    {
                        "label": render_any(item.get("label") or "", context),
                        "status": item.get("status") or "completed",
                        "description": render_any(item.get("description") or "", context),
                    }
                    for item in card.get("timeline", [])
                ],
            )
        )

    approval_panel = dict(ui_config.get("approval_panel") or {})
    if approval_panel and policy_decision.requires_human_review:
        request_id = str(resolve_path("request.requestId", context) or "")
        events.append(
            build_generic_approval_panel(
                scenario_id=scenario.id,
                approval_type=str(runtime.get("approval_type") or scenario.id),
                request_id=request_id,
                thread_id=thread_id,
                title=render_any(approval_panel.get("title") or f"{scenario.name} Approval", context),
                requester_id=requester_id,
                review_roles=list(scenario.hitl.review_roles),
                approval_chain=[
                    {
                        "id": stage.id,
                        "name": stage.name,
                        "roles": list(stage.roles),
                        "required": stage.required,
                    }
                    for stage in scenario.hitl.approval_chain
                ],
                current_status=str(resolve_path("request.status", context) or "pending_review"),
                policy_decision=policy_decision,
                approve_label=render_any(approval_panel.get("approve_label") or "Approve", context),
                reject_label=render_any(approval_panel.get("reject_label") or "Reject", context),
            )
        )

    return events


def approval_line(runtime: Mapping[str, Any], decision: PolicyDecision) -> str:
    messages = dict(runtime.get("approval_messages") or {})
    key = "requires_human" if decision.requires_human_review else "auto"
    return str(messages.get(key) or decision.reason)


def resolve_value(value: Any, context: Mapping[str, Any]) -> Any:
    if isinstance(value, str) and value.startswith("$"):
        return resolve_path(value[1:], context)
    if isinstance(value, str):
        return render_template(value, context)
    return value


def resolve_path(path: str, context: Mapping[str, Any]) -> Any:
    current: Any = context
    for part in path.split("."):
        if isinstance(current, Mapping):
            current = current.get(part)
        else:
            current = getattr(current, part, None)
        if current is None:
            return None
    return current


def render_any(value: Any, context: Mapping[str, Any]) -> str:
    resolved = resolve_value(value, context)
    return "" if resolved is None else str(resolved)


def render_template(template: str, context: Mapping[str, Any]) -> str:
    def replace(match: re.Match[str]) -> str:
        value = resolve_path(match.group(1), context)
        return "" if value is None else str(value)

    return re.sub(r"\{([A-Za-z0-9_.]+)\}", replace, template)


def format_display_value(value: Any, config: Mapping[str, Any]) -> Any:
    if config.get("format") == "currency_cny":
        return f"¥{float(value or 0):.2f}"
    return value
