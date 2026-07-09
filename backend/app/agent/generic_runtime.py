"""Config-driven runtime for simple supervisor scenarios.

This runtime intentionally supports a small set of deterministic building
blocks first: slot extraction, one Tool Gateway call, one policy binding,
standard business UI events, and a final response template. That is enough to
move permission and reimbursement flows away from scenario-specific node code.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Mapping

from app.agent.planner import ERP_READONLY_TOOLS, plan_and_execute
from app.agent.scenario_registry import ScenarioConfig, get_default_registry
from app.agent.state import AgentState
from app.agent.tool_gateway import execute_tool, gateway_context_from_state
from app.core.config import get_settings
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
    slot_config = dict(runtime.get("slot_extraction") or {})
    slots = await extract_slots_smart(message, slot_config, state)

    # A7 完整形态：必填 slot 缺失时中断对话向用户追问（interrupt 语义与审批
    # 一致），拿到补充信息后合并重提取。只追问一轮，仍缺失则按默认值继续。
    slots, message = await fill_missing_required_slots(
        message=message,
        slots=slots,
        slot_config=slot_config,
        state=state,
        scenario=scenario,
    )

    base_context = {
        "input": {"message": message},
        "slots": slots,
        "context": {"user_id": requester_id, "thread_id": thread_id},
        "scenario": scenario.to_dict(),
    }

    policy_configs = list(runtime.get("policies") or [runtime.get("policy", {})])
    policy_decisions = [
        evaluate_runtime_policy(config, base_context)
        for config in policy_configs
        if config
    ]
    policy_decision = combine_policy_decisions(policy_decisions)
    if not policy_decision.allowed:
        return {
            "intent": scenario.id,
            "scenario_id": scenario.id,
            "error_message": policy_decision.reason or f"{scenario.name} was denied by policy.",
            "policy_events": [decision.to_audit_event() for decision in policy_decisions],
            "current_step": f"{scenario.id}_policy_denied",
        }

    # A4：声明了 planner.enabled 的场景（且全局 AGENT_PLANNER_ENABLED=1）
    # 由 LLM 在场景工具白名单内规划执行序列；每步仍经 tool_gateway 全量治理。
    # 规划失败/计划无效/LLM 不可用 → 无损降级回下方静态配置的工具序列。
    tool_results = []
    planner_config = dict(runtime.get("planner") or {})
    if planner_config.get("enabled") and get_settings().agent_planner_enabled:
        allowed = set(scenario.tools) or set(TOOL_HANDLERS)
        # F4：全局开启 ERP 只读扩面时，把 ERP 读工具并入规划器可用集合。
        # 这些工具走 async 连接器治理路径，无需本地 handler。
        if get_settings().planner_erp_readonly_enabled:
            allowed = allowed | ERP_READONLY_TOOLS
        plan_execution = await plan_and_execute(
            state,
            scenario_id=scenario.id,
            goal=message,
            slots=slots,
            allowed_tools=allowed,
            handlers=TOOL_HANDLERS,
            dry_run=dry_run,
        )
        if plan_execution.used_planner:
            tool_results = list(plan_execution.results)
            if not plan_execution.success:
                return {
                    "intent": scenario.id,
                    "scenario_id": scenario.id,
                    "error_message": plan_execution.aborted_reason
                    or f"{scenario.name} planned execution failed.",
                    "tool_gateway_events": [item.audit_event for item in tool_results],
                    "policy_events": [
                        decision.to_audit_event() for decision in policy_decisions
                    ],
                    "current_step": f"{scenario.id}_plan_failed",
                }
        else:
            logger.info(
                "planner_fallback_static_tools",
                scenario=scenario.id,
                degraded=plan_execution.degraded,
                reason=plan_execution.aborted_reason,
            )

    tool_configs = list(runtime.get("tools") or [runtime.get("tool", {})]) if not tool_results else []
    for tool_config in (config for config in tool_configs if config):
        execution_context = {
            **base_context,
            "tool_results": [dict(item.data or {}) for item in tool_results],
        }
        tool_result = execute_runtime_tool(
            state=state,
            scenario=scenario,
            runtime=runtime,
            tool_config=tool_config,
            context=execution_context,
            dry_run=dry_run,
        )
        tool_results.append(tool_result)
        if not tool_result.success:
            return {
                "intent": scenario.id,
                "scenario_id": scenario.id,
                "error_message": tool_result.error or f"{scenario.name} tool execution failed.",
                "tool_gateway_events": [item.audit_event for item in tool_results],
                "policy_events": [decision.to_audit_event() for decision in policy_decisions],
                "current_step": f"{scenario.id}_error",
            }

    if not tool_results:
        raise ValueError(f"Scenario '{scenario.id}' does not define runtime tools.")
    # ReAct 模式允许"失败→补救"，首个结果可能是失败步；业务数据取首个成功结果
    primary_result = next(
        (item for item in tool_results if item.success), tool_results[0]
    )
    request_data = dict(primary_result.data or {})
    request_data["toolResults"] = [
        dict(item.data or {}) for item in tool_results if item.success
    ]
    request_data["requestId"] = request_data.get("requestId") or f"DRY_RUN_{scenario.id.upper()}"
    request_data["approvalRequired"] = policy_decision.requires_human_review
    request_data["policyDecision"] = policy_decision.to_audit_event()
    request_data["status"] = "pending_review" if policy_decision.requires_human_review else "auto_approved"
    request_data.setdefault("currency", "CNY")
    if scenario.id == "reimbursement":
        request_data.setdefault("costCenterId", "CC-SUPPORT")

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
        "policy_events": [decision.to_audit_event() for decision in policy_decisions],
        "tool_gateway_events": [item.audit_event for item in tool_results],
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


# ── A7: LLM function-calling slot 提取（正则保留为逐字段降级路径）──────────


def _slot_json_schema(fields: Mapping[str, Any]) -> dict[str, Any]:
    """把场景 slot 配置编译成 JSON Schema，交给 LLM 结构化输出。"""
    properties: dict[str, Any] = {}
    for name, field_config in fields.items():
        strategy = str(field_config.get("type") or "message_excerpt")
        if strategy == "amount":
            properties[name] = {"type": "number", "description": "金额，数字"}
        elif strategy == "keyword_enum":
            values = [
                option.get("value")
                for option in field_config.get("options", [])
                if option.get("value") is not None
            ]
            properties[name] = {"type": "string", "enum": [str(v) for v in values]}
        elif strategy == "keyword_map":
            values = [
                pattern.get("value")
                for pattern in field_config.get("patterns", [])
                if pattern.get("value") is not None
            ]
            prop: dict[str, Any] = {"type": "string"}
            if values:
                prop["enum"] = [str(v) for v in values]
            properties[name] = prop
        else:
            properties[name] = {"type": "string"}
    return {
        "title": "ScenarioSlots",
        "type": "object",
        "properties": properties,
    }


def _coerce_slot_value(value: Any, field_config: Mapping[str, Any]) -> Any:
    """把 LLM 输出的值收敛到配置约束内；不合法返回 None（回落正则值）。"""
    if value in (None, ""):
        return None
    strategy = str(field_config.get("type") or "message_excerpt")
    if strategy == "amount":
        try:
            amount = Decimal(str(value)).quantize(Decimal("0.01"))
        except (InvalidOperation, ValueError):
            return None
        return amount if amount >= 0 else None
    if strategy == "keyword_enum":
        allowed = {
            str(option.get("value"))
            for option in field_config.get("options", [])
            if option.get("value") is not None
        }
        return str(value) if str(value) in allowed else None
    if strategy == "keyword_map":
        allowed = {
            str(pattern.get("value"))
            for pattern in field_config.get("patterns", [])
            if pattern.get("value") is not None
        }
        if allowed and str(value) not in allowed:
            return None
        return str(value)
    if strategy == "message_excerpt":
        max_length = int(field_config.get("max_length") or 500)
        return str(value)[:max_length]
    return value


async def _llm_extract_slots(
    message: str,
    fields: Mapping[str, Any],
    state: AgentState,
) -> dict[str, Any] | None:
    """LLM 结构化 slot 提取；任何失败返回 None（调用方整体回落正则）。"""
    from langchain_core.messages import HumanMessage, SystemMessage

    from app.agent.dependencies import get_agent_dependencies
    from app.llm.gateway import LLMCallContext

    try:
        response = await get_agent_dependencies().llm.runnable(
            "slot_extraction",
            context=LLMCallContext(
                thread_id=str(get_state_val(state, "thread_id", "unknown")),
                trace_id=str(get_state_val(state, "trace_id", "") or "") or None,
                tenant_id=str(get_state_val(state, "tenant_id", "") or "") or None,
            ),
            schema=_slot_json_schema(fields),
            temperature=0.0,
            timeout_seconds=6.0,
        ).ainvoke([
            SystemMessage(
                content=(
                    "从用户消息中提取业务字段。不确定的字段返回空字符串，"
                    "不要编造。金额只取数字。"
                )
            ),
            HumanMessage(content=message),
        ])
        if isinstance(response, dict):
            return response
        dumped = getattr(response, "model_dump", None)
        return dumped() if callable(dumped) else None
    except Exception as exc:
        logger.warning("llm_slot_extraction_failed_fallback_regex", error=str(exc))
        return None


def _slot_value_missing(value: Any, field_config: Mapping[str, Any]) -> bool:
    """判断一个 slot 值是否算"缺失"（用于必填校验）。"""
    if value in (None, ""):
        return True
    strategy = str(field_config.get("type") or "message_excerpt")
    if strategy == "amount":
        try:
            return Decimal(str(value)) <= 0
        except (InvalidOperation, ValueError):
            return True
    return False


async def fill_missing_required_slots(
    *,
    message: str,
    slots: dict[str, Any],
    slot_config: Mapping[str, Any],
    state: AgentState,
    scenario: ScenarioConfig,
) -> tuple[dict[str, Any], str]:
    """A7 完整形态：必填 slot 缺失 → interrupt 追问 → 合并重提取（仅一轮）。

    返回 (slots, message)。message 可能被追加了用户补充内容，供后续
    message_excerpt 类字段与审计使用。未启用开关或无缺失时原样返回。
    """
    from langgraph.types import interrupt

    if not get_settings().slot_clarification_enabled:
        return slots, message
    fields = dict(slot_config.get("fields") or {})
    missing = [
        name
        for name, field_config in fields.items()
        if field_config.get("required")
        and _slot_value_missing(slots.get(name), field_config)
    ]
    if not missing:
        return slots, message

    labels = [
        str(fields[name].get("label") or fields[name].get("description") or name)
        for name in missing
    ]
    resume_value = interrupt(
        {
            "kind": "slot_filling",
            "question": f"办理「{scenario.name}」还需要补充：{'、'.join(labels)}",
            "missing_fields": missing,
            "missing_labels": labels,
            "scenario_id": scenario.id,
            "thread_id": str(get_state_val(state, "thread_id", "") or ""),
        }
    )
    if isinstance(resume_value, Mapping):
        answer = str(resume_value.get("answer") or "")
    else:
        answer = str(resume_value or "")
    if not answer:
        return slots, message

    combined = f"{message}\n（用户补充：{answer}）"
    refreshed = await extract_slots_smart(combined, slot_config, state)
    merged = dict(slots)
    for name, field_config in fields.items():
        if _slot_value_missing(merged.get(name), field_config) and not _slot_value_missing(
            refreshed.get(name), field_config
        ):
            merged[name] = refreshed[name]
    still_missing = [
        name
        for name in missing
        if _slot_value_missing(merged.get(name), fields[name])
    ]
    if still_missing:
        logger.info(
            "slot_filling_partial",
            scenario=scenario.id,
            still_missing=still_missing,
        )
    return merged, combined


async def extract_slots_smart(
    message: str,
    config: Mapping[str, Any],
    state: AgentState,
) -> dict[str, Any]:
    """A7 入口：LLM 提取（开关开启时）→ 逐字段校验收敛 → 正则兜底。

    合并规则：LLM 值先经 _coerce_slot_value 收敛到配置约束（enum 白名单、
    金额非负），不合法或缺失的字段用正则结果补位——保证启用 LLM 后
    最坏情况也不会差于纯正则。
    """
    regex_slots = extract_slots(message, config)
    fields = dict(config.get("fields") or {})
    if not fields or not get_settings().slot_llm_extraction_enabled:
        return regex_slots

    llm_slots = await _llm_extract_slots(message, fields, state)
    if llm_slots is None:
        return regex_slots

    merged: dict[str, Any] = {}
    for name, field_config in fields.items():
        coerced = _coerce_slot_value(llm_slots.get(name), field_config)
        merged[name] = coerced if coerced is not None else regex_slots.get(name)
    return merged


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
        raw = match.group(1) if match else field_config.get("default", 0)
        try:
            return Decimal(str(raw)).quantize(Decimal("0.01"))
        except (InvalidOperation, ValueError):
            return Decimal("0.00")

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
    args.setdefault("routing_key", str(resolve_path("context.thread_id", context) or "global"))
    return handler(**args)


def combine_policy_decisions(decisions: list[PolicyDecision]) -> PolicyDecision:
    if not decisions:
        raise ValueError("Configured scenario must define at least one policy binding.")
    if len(decisions) == 1:
        return decisions[0]
    allowed = all(decision.allowed for decision in decisions)
    review = any(decision.requires_human_review for decision in decisions)
    return PolicyDecision(
        policy_version="+".join(dict.fromkeys(decision.policy_version for decision in decisions)),
        effect="DENY" if not allowed else "REVIEW" if review else "ALLOW",
        allowed=allowed,
        requires_human_review=review,
        matched_rules=tuple(
            rule for decision in decisions for rule in decision.matched_rules
        ),
        reason="; ".join(decision.reason for decision in decisions if decision.reason),
        policy_variant="+".join(dict.fromkeys(decision.policy_variant for decision in decisions)),
        rollout_bucket=decisions[0].rollout_bucket,
    )


def execute_runtime_tool(
    *,
    state: AgentState,
    scenario: ScenarioConfig,
    runtime: Mapping[str, Any],
    tool_config: Mapping[str, Any] | None = None,
    context: Mapping[str, Any],
    dry_run: bool = False,
):
    tool_config = dict(tool_config or runtime.get("tool") or {})
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
