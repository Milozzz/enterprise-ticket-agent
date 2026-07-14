"""Validation gates for configurable supervisor scenarios."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Iterable, Mapping

from app.agent.generic_runtime import list_runtime_policy_names, list_runtime_tool_names
from app.agent.scenario_graph_runtime import (
    END_TOKEN,
    list_graph_node_handlers,
    list_graph_routers,
)
from app.agent.scenario_registry import ScenarioConfig
from app.agent.tool_gateway import TOOL_SPECS
from app.agent.workflow_factory import WORKFLOW_ENTRYPOINTS
from app.core.policy import load_policy


KNOWN_ROLES = {"USER", "AGENT", "MANAGER", "SECURITY", "FINANCE"}
SUPPORTED_SLOT_EXTRACTORS = {"keyword_map", "keyword_enum", "amount", "message_excerpt"}
CONFIG_DRIVEN_ENTRYPOINTS = {"permission_request", "reimbursement", "configured_runtime"}
TEMPLATE_REFERENCE_RE = re.compile(r"\{([A-Za-z0-9_.]+)\}")


@dataclass(frozen=True)
class ScenarioValidationIssue:
    severity: str
    code: str
    path: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {
            "severity": self.severity,
            "code": self.code,
            "path": self.path,
            "message": self.message,
        }


@dataclass(frozen=True)
class ScenarioValidationReport:
    scenario_id: str
    valid: bool
    error_count: int
    warning_count: int
    issues: tuple[ScenarioValidationIssue, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "valid": self.valid,
            "error_count": self.error_count,
            "warning_count": self.warning_count,
            "issues": [issue.to_dict() for issue in self.issues],
        }


def validate_scenario_config(config: ScenarioConfig) -> ScenarioValidationReport:
    issues: list[ScenarioValidationIssue] = []
    _validate_scenario_shell(config, issues)
    _validate_runtime(config, issues)

    error_count = sum(1 for issue in issues if issue.severity == "error")
    warning_count = sum(1 for issue in issues if issue.severity == "warning")
    return ScenarioValidationReport(
        scenario_id=config.id,
        valid=error_count == 0,
        error_count=error_count,
        warning_count=warning_count,
        issues=tuple(issues),
    )


def summarize_validation(reports: Iterable[ScenarioValidationReport]) -> dict[str, int]:
    report_list = list(reports)
    return {
        "scenario_count": len(report_list),
        "valid_count": sum(1 for report in report_list if report.valid),
        "error_count": sum(report.error_count for report in report_list),
        "warning_count": sum(report.warning_count for report in report_list),
    }


def _validate_scenario_shell(config: ScenarioConfig, issues: list[ScenarioValidationIssue]) -> None:
    if not config.workflow.strip():
        _error(issues, "workflow.missing", "workflow", "Workflow identifier is required.")

    if config.status not in {"active", "draft", "paused"}:
        _error(issues, "status.invalid", "status", f"Unsupported scenario status '{config.status}'.")

    if config.status == "active" and not config.keywords:
        _warning(
            issues,
            "routing.keywords.missing",
            "keywords",
            "Active scenarios should define keywords so the supervisor can route to them deterministically.",
        )

    for role in config.allowed_roles:
        if role not in KNOWN_ROLES:
            _warning(issues, "role.unknown", "allowed_roles", f"Role '{role}' is not in the platform role catalog.")

    if config.hitl.enabled and not config.hitl.review_roles:
        _error(
            issues,
            "hitl.review_roles.missing",
            "hitl.review_roles",
            "HITL is enabled but no review role is configured.",
        )

    for role in config.hitl.review_roles:
        if role not in KNOWN_ROLES:
            _warning(
                issues,
                "hitl.review_role.unknown",
                "hitl.review_roles",
                f"Review role '{role}' is not in the platform role catalog.",
            )

    for index, stage in enumerate(config.hitl.approval_chain):
        stage_path = f"hitl.approval_chain[{index}]"
        if not stage.id:
            _error(issues, "hitl.approval_stage.id_missing", f"{stage_path}.id", "Approval stage id is required.")
        if not stage.roles:
            _error(
                issues,
                "hitl.approval_stage.roles_missing",
                f"{stage_path}.roles",
                "Approval stage must declare at least one reviewer role.",
            )
        for role in stage.roles:
            if role not in KNOWN_ROLES:
                _warning(
                    issues,
                    "hitl.approval_stage.role_unknown",
                    f"{stage_path}.roles",
                    f"Approval stage role '{role}' is not in the platform role catalog.",
                )

    policy = load_policy()
    policy_actions = set(policy.get("actions", {}))
    review_policies = _review_policy_names(policy)

    for tool_name in config.tools:
        spec = TOOL_SPECS.get(tool_name)
        if spec is None:
            _error(issues, "tool.unknown", "tools", f"Tool '{tool_name}' is not registered in Tool Gateway.")
            continue
        if spec.action not in policy_actions:
            _warning(
                issues,
                "tool.action_policy.missing",
                "tools",
                f"Tool '{tool_name}' maps to action '{spec.action}', but no action policy exists.",
            )

    for policy_name in config.policies:
        if policy_name not in review_policies:
            _error(
                issues,
                "policy.unknown",
                "policies",
                f"Policy '{policy_name}' is not registered in Policy-as-Code review policies.",
            )


def _validate_runtime(config: ScenarioConfig, issues: list[ScenarioValidationIssue]) -> None:
    runtime = config.runtime or {}
    entrypoint = WORKFLOW_ENTRYPOINTS.get(config.workflow)

    if not runtime:
        if config.id != "refund" or entrypoint in CONFIG_DRIVEN_ENTRYPOINTS:
            _error(
                issues,
                "runtime.required",
                "runtime",
                f"Workflow '{config.workflow}' uses generic runtime entrypoint '{entrypoint}', so runtime config is required.",
            )
        return

    schema_version = str(runtime.get("schema_version") or "")
    if schema_version not in {"2", "3"}:
        _error(
            issues,
            "runtime.schema_version.invalid",
            "runtime.schema_version",
            "Configurable runtime supports schema_version '2' and declarative graph version '3'.",
        )
        return
    if schema_version == "3":
        _validate_graph_runtime(config, runtime, issues)
        handlers = {
            str(node.get("handler") or "")
            for node in runtime.get("nodes") or []
            if isinstance(node, Mapping)
        }
        if "configured_prepare" in handlers:
            slot_fields = _validate_slot_extraction(runtime, issues)
            _validate_runtime_tool(config, runtime, slot_fields, issues)
            _validate_runtime_policy(config, runtime, slot_fields, issues)
            _validate_runtime_response(runtime, slot_fields, issues)
            _validate_runtime_ui(config, runtime, slot_fields, issues)
        return

    slot_fields = _validate_slot_extraction(runtime, issues)
    _validate_runtime_tool(config, runtime, slot_fields, issues)
    _validate_runtime_policy(config, runtime, slot_fields, issues)
    _validate_runtime_response(runtime, slot_fields, issues)
    _validate_runtime_ui(config, runtime, slot_fields, issues)


def _validate_graph_runtime(
    config: ScenarioConfig,
    runtime: Mapping[str, Any],
    issues: list[ScenarioValidationIssue],
) -> None:
    if runtime.get("engine") != "langgraph":
        _error(
            issues,
            "runtime.graph.engine.invalid",
            "runtime.engine",
            "Runtime v3 requires engine 'langgraph'.",
        )

    raw_nodes = runtime.get("nodes")
    if not isinstance(raw_nodes, list) or not raw_nodes:
        _error(issues, "runtime.graph.nodes.missing", "runtime.nodes", "Graph nodes are required.")
        return

    node_ids: set[str] = set()
    handlers = list_graph_node_handlers()
    for index, node in enumerate(raw_nodes):
        path = f"runtime.nodes[{index}]"
        if not isinstance(node, Mapping):
            _error(issues, "runtime.graph.node.invalid", path, "Graph node must be an object.")
            continue
        node_id = str(node.get("id") or "")
        handler = str(node.get("handler") or "")
        if not node_id:
            _error(issues, "runtime.graph.node.id_missing", f"{path}.id", "Node id is required.")
        elif node_id in node_ids:
            _error(issues, "runtime.graph.node.duplicate", f"{path}.id", f"Duplicate node id '{node_id}'.")
        else:
            node_ids.add(node_id)
        if handler not in handlers:
            _error(
                issues,
                "runtime.graph.node.handler_unknown",
                f"{path}.handler",
                f"Node handler '{handler}' is not in the trusted runtime catalog.",
            )

    entry = str(runtime.get("entry_node") or "")
    if entry not in node_ids:
        _error(
            issues,
            "runtime.graph.entry.invalid",
            "runtime.entry_node",
            f"Entry node '{entry}' is not declared.",
        )

    outgoing: set[str] = set()
    for index, edge in enumerate(runtime.get("edges") or []):
        path = f"runtime.edges[{index}]"
        if not isinstance(edge, Mapping):
            _error(issues, "runtime.graph.edge.invalid", path, "Edge must be an object.")
            continue
        source = str(edge.get("from") or "")
        target = str(edge.get("to") or "")
        _validate_graph_endpoint(source, node_ids, f"{path}.from", issues, allow_end=False)
        _validate_graph_endpoint(target, node_ids, f"{path}.to", issues, allow_end=True)
        if source:
            outgoing.add(source)

    routers = list_graph_routers()
    for index, conditional in enumerate(runtime.get("conditional_edges") or []):
        path = f"runtime.conditional_edges[{index}]"
        if not isinstance(conditional, Mapping):
            _error(issues, "runtime.graph.conditional.invalid", path, "Conditional edge must be an object.")
            continue
        source = str(conditional.get("from") or "")
        router = str(conditional.get("router") or "")
        _validate_graph_endpoint(source, node_ids, f"{path}.from", issues, allow_end=False)
        if router not in routers:
            _error(
                issues,
                "runtime.graph.router.unknown",
                f"{path}.router",
                f"Router '{router}' is not in the trusted runtime catalog.",
            )
        routes = conditional.get("routes")
        if not isinstance(routes, Mapping) or not routes:
            _error(issues, "runtime.graph.routes.missing", f"{path}.routes", "Router outcomes are required.")
        else:
            for outcome, target in routes.items():
                _validate_graph_endpoint(
                    str(target),
                    node_ids,
                    f"{path}.routes.{outcome}",
                    issues,
                    allow_end=True,
                )
        if source:
            outgoing.add(source)

    for node_id in sorted(node_ids - outgoing):
        _warning(
            issues,
            "runtime.graph.node.terminal_implicit",
            f"runtime.nodes.{node_id}",
            "Node has no outgoing edge; declare an explicit $end edge for auditability.",
        )


def _validate_graph_endpoint(
    value: str,
    node_ids: set[str],
    path: str,
    issues: list[ScenarioValidationIssue],
    *,
    allow_end: bool,
) -> None:
    if allow_end and value == END_TOKEN:
        return
    if value not in node_ids:
        _error(
            issues,
            "runtime.graph.endpoint.unknown",
            path,
            f"Graph endpoint '{value}' is not a declared node.",
        )


def _validate_slot_extraction(runtime: Mapping[str, Any], issues: list[ScenarioValidationIssue]) -> set[str]:
    slot_config = runtime.get("slot_extraction")
    if not isinstance(slot_config, Mapping):
        _error(issues, "runtime.slots.missing", "runtime.slot_extraction", "runtime.slot_extraction must be an object.")
        return set()

    fields = slot_config.get("fields")
    if not isinstance(fields, Mapping) or not fields:
        _error(
            issues,
            "runtime.slots.fields_missing",
            "runtime.slot_extraction.fields",
            "runtime.slot_extraction.fields must define at least one slot.",
        )
        return set()

    field_names = {str(name) for name in fields}
    for field_name, raw_field in fields.items():
        path = f"runtime.slot_extraction.fields.{field_name}"
        if not isinstance(raw_field, Mapping):
            _error(issues, "runtime.slot.field.invalid", path, "Slot field config must be an object.")
            continue

        extractor_type = str(raw_field.get("type") or "message_excerpt")
        if extractor_type not in SUPPORTED_SLOT_EXTRACTORS:
            _error(
                issues,
                "runtime.slot.extractor.unknown",
                f"{path}.type",
                f"Unsupported slot extractor '{extractor_type}'.",
            )

        if extractor_type == "amount":
            regex = raw_field.get("regex")
            if not regex:
                _warning(issues, "runtime.slot.amount.regex_missing", path, "Amount extractor should define a regex.")
            else:
                _validate_regex(str(regex), f"{path}.regex", issues)

        if extractor_type == "keyword_map" and not raw_field.get("patterns") and not raw_field.get("fallback_regex"):
            _warning(
                issues,
                "runtime.slot.keyword_map.patterns_missing",
                path,
                "keyword_map should define patterns or fallback_regex for reliable extraction.",
            )

        fallback_regex = raw_field.get("fallback_regex")
        if fallback_regex:
            _validate_regex(str(fallback_regex), f"{path}.fallback_regex", issues)

        if extractor_type == "keyword_enum" and not raw_field.get("options"):
            _warning(issues, "runtime.slot.keyword_enum.options_missing", path, "keyword_enum should define options.")

    return field_names


def _validate_runtime_tool(
    config: ScenarioConfig,
    runtime: Mapping[str, Any],
    slot_fields: set[str],
    issues: list[ScenarioValidationIssue],
) -> None:
    raw_tools = runtime.get("tools") or [runtime.get("tool")]
    tools = [tool for tool in raw_tools if isinstance(tool, Mapping)]
    if not tools:
        _error(issues, "runtime.tool.missing", "runtime.tool", "runtime.tool must be configured.")
        return

    for index, tool in enumerate(tools):
        path = "runtime.tool" if len(tools) == 1 else f"runtime.tools[{index}]"
        _validate_runtime_tool_binding(config, tool, path, slot_fields, issues)


def _validate_runtime_tool_binding(
    config: ScenarioConfig,
    tool: Mapping[str, Any],
    path: str,
    slot_fields: set[str],
    issues: list[ScenarioValidationIssue],
) -> None:

    tool_name = str(tool.get("name") or "")
    if not tool_name:
        _error(issues, "runtime.tool.name_missing", f"{path}.name", "runtime tool name is required.")
    elif tool_name not in list_runtime_tool_names():
        _error(
            issues,
            "runtime.tool.unknown",
            f"{path}.name",
            f"Runtime tool '{tool_name}' has no generic runtime handler.",
        )
    elif tool_name not in config.tools:
        _error(
            issues,
            "runtime.tool.not_declared",
            f"{path}.name",
            f"Runtime tool '{tool_name}' must also be declared in scenario.tools.",
        )

    args = tool.get("args")
    if not isinstance(args, Mapping) or not args:
        _error(issues, "runtime.tool.args_missing", f"{path}.args", "runtime tool args must be mapped.")
    else:
        _validate_references(args, f"{path}.args", slot_fields, {"slots", "context", "scenario", "input", "tool_results"}, issues)


def _validate_runtime_policy(
    config: ScenarioConfig,
    runtime: Mapping[str, Any],
    slot_fields: set[str],
    issues: list[ScenarioValidationIssue],
) -> None:
    raw_policies = runtime.get("policies") or [runtime.get("policy")]
    policies = [policy for policy in raw_policies if isinstance(policy, Mapping)]
    if not policies:
        _error(issues, "runtime.policy.missing", "runtime.policy", "runtime.policy must be configured.")
        return

    for index, policy in enumerate(policies):
        path = "runtime.policy" if len(policies) == 1 else f"runtime.policies[{index}]"
        _validate_runtime_policy_binding(config, policy, path, slot_fields, issues)


def _validate_runtime_policy_binding(
    config: ScenarioConfig,
    policy: Mapping[str, Any],
    path: str,
    slot_fields: set[str],
    issues: list[ScenarioValidationIssue],
) -> None:

    policy_name = str(policy.get("name") or "")
    if not policy_name:
        _error(issues, "runtime.policy.name_missing", f"{path}.name", "runtime policy name is required.")
    elif policy_name not in list_runtime_policy_names():
        _error(
            issues,
            "runtime.policy.unknown",
            f"{path}.name",
            f"Runtime policy '{policy_name}' has no generic runtime evaluator.",
        )
    elif policy_name not in config.policies:
        _error(
            issues,
            "runtime.policy.not_declared",
            f"{path}.name",
            f"Runtime policy '{policy_name}' must also be declared in scenario.policies.",
        )

    args = policy.get("args")
    if not isinstance(args, Mapping) or not args:
        _error(issues, "runtime.policy.args_missing", f"{path}.args", "runtime policy args must be mapped.")
    else:
        _validate_references(args, f"{path}.args", slot_fields, {"slots", "context", "scenario", "input"}, issues)


def _validate_runtime_response(
    runtime: Mapping[str, Any],
    slot_fields: set[str],
    issues: list[ScenarioValidationIssue],
) -> None:
    if not runtime.get("reply_template"):
        _warning(
            issues,
            "runtime.reply_template.missing",
            "runtime.reply_template",
            "reply_template is recommended so the scenario returns a business-readable response.",
        )
    else:
        _validate_references(
            runtime.get("reply_template"),
            "runtime.reply_template",
            slot_fields,
            {"slots", "context", "scenario", "input", "request", "policy", "approval_line"},
            issues,
        )

    state_outputs = runtime.get("state_outputs")
    if state_outputs is not None:
        if not isinstance(state_outputs, Mapping):
            _error(issues, "runtime.state_outputs.invalid", "runtime.state_outputs", "state_outputs must be an object.")
        else:
            _validate_references(
                state_outputs,
                "runtime.state_outputs",
                slot_fields,
                {"slots", "context", "scenario", "input", "request", "policy"},
                issues,
            )


def _validate_runtime_ui(
    config: ScenarioConfig,
    runtime: Mapping[str, Any],
    slot_fields: set[str],
    issues: list[ScenarioValidationIssue],
) -> None:
    ui = runtime.get("ui")
    if not isinstance(ui, Mapping):
        _warning(issues, "runtime.ui.missing", "runtime.ui", "runtime.ui is recommended for Generative UI cards.")
        return

    if not ui.get("business_request_card"):
        _warning(
            issues,
            "runtime.ui.business_card.missing",
            "runtime.ui.business_request_card",
            "business_request_card is recommended so users can inspect the generated business request.",
        )

    if config.hitl.enabled and not ui.get("approval_panel"):
        _warning(
            issues,
            "runtime.ui.approval_panel.missing",
            "runtime.ui.approval_panel",
            "HITL is enabled; approval_panel is recommended for reviewer action.",
        )

    _validate_references(
        ui,
        "runtime.ui",
        slot_fields,
        {"slots", "context", "scenario", "input", "request", "policy", "approval_line"},
        issues,
        severity="warning",
    )


def _validate_references(
    value: Any,
    path: str,
    slot_fields: set[str],
    allowed_roots: set[str],
    issues: list[ScenarioValidationIssue],
    *,
    severity: str = "error",
) -> None:
    for ref_path, ref in _iter_references(value, path):
        root = ref.split(".", 1)[0]
        if root not in allowed_roots:
            _add_issue(
                issues,
                severity,
                "runtime.reference.root_unknown",
                ref_path,
                f"Reference '${ref}' uses unsupported root '{root}'.",
            )
            continue
        if root == "slots":
            parts = ref.split(".")
            if len(parts) < 2 or parts[1] not in slot_fields:
                _add_issue(
                    issues,
                    severity,
                    "runtime.reference.slot_unknown",
                    ref_path,
                    f"Reference '${ref}' points to an undefined slot.",
                )


def _iter_references(value: Any, path: str) -> Iterable[tuple[str, str]]:
    if isinstance(value, str):
        if value.startswith("$"):
            yield path, value[1:]
        for match in TEMPLATE_REFERENCE_RE.finditer(value):
            yield path, match.group(1)
        return

    if isinstance(value, Mapping):
        for key, child in value.items():
            yield from _iter_references(child, f"{path}.{key}")
        return

    if isinstance(value, list):
        for index, child in enumerate(value):
            yield from _iter_references(child, f"{path}[{index}]")


def _validate_regex(pattern: str, path: str, issues: list[ScenarioValidationIssue]) -> None:
    try:
        compiled = re.compile(pattern)
    except re.error as exc:
        _error(issues, "runtime.regex.invalid", path, f"Regex is invalid: {exc}")
        return
    if compiled.groups < 1:
        _warning(
            issues,
            "runtime.regex.no_capture_group",
            path,
            "Regex should expose at least one capture group because runtime extractors read group(1).",
        )


def _review_policy_names(policy: Mapping[str, Any]) -> set[str]:
    return {key for key, value in policy.items() if key.endswith("_review") and isinstance(value, Mapping)}


def _error(issues: list[ScenarioValidationIssue], code: str, path: str, message: str) -> None:
    _add_issue(issues, "error", code, path, message)


def _warning(issues: list[ScenarioValidationIssue], code: str, path: str, message: str) -> None:
    _add_issue(issues, "warning", code, path, message)


def _add_issue(
    issues: list[ScenarioValidationIssue],
    severity: str,
    code: str,
    path: str,
    message: str,
) -> None:
    issues.append(ScenarioValidationIssue(severity=severity, code=code, path=path, message=message))
