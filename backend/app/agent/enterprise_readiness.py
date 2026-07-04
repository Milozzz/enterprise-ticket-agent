"""Continuous governance checks for enterprise agent releases."""

from __future__ import annotations

from typing import Any

from app.agent.mcp_adapter import EXECUTABLE_MCP_TOOLS
from app.agent.scenario_eval import run_all_scenario_evals
from app.agent.tool_gateway import TOOL_SPECS, ToolSideEffect
from app.core.policy import load_policy
from app.erp.runtime import load_connector_runtime_config


async def run_enterprise_readiness_eval(
    connector_id: str = "CONN-SAP-ODATA-DEMO",
) -> dict[str, Any]:
    policy = load_policy()
    action_names = set(policy.get("actions", {}))
    tool_actions = {spec.action for spec in TOOL_SPECS.values()}
    missing_actions = sorted(tool_actions - action_names)
    erp_write_tools = [
        spec
        for spec in TOOL_SPECS.values()
        if spec.name.startswith("erp_") and spec.side_effect == ToolSideEffect.WRITE
    ]
    scenario_evals = await run_all_scenario_evals()
    connector = await load_connector_runtime_config(connector_id)

    checks = [
        _check(
            "policy_fail_closed",
            policy.get("default_action_effect") == "deny",
            "Unknown actions are denied by default.",
            severity="required",
        ),
        _check(
            "tool_policy_coverage",
            not missing_actions,
            "Every Tool Gateway action has an explicit policy binding.",
            details={"missing_actions": missing_actions},
            severity="required",
        ),
        _check(
            "erp_write_approval",
            all(spec.approval_required for spec in erp_write_tools),
            "Every ERP write requires approval evidence.",
            severity="required",
        ),
        _check(
            "erp_write_idempotency",
            all(spec.idempotency_fields for spec in erp_write_tools),
            "Every ERP write defines deterministic idempotency fields.",
            severity="required",
        ),
        _check(
            "mcp_runtime_coverage",
            {spec.name for spec in TOOL_SPECS.values() if spec.name.startswith("erp_")}
            <= EXECUTABLE_MCP_TOOLS,
            "All ERP tools exposed through MCP have executable runtime handlers.",
            severity="required",
        ),
        _check(
            "scenario_regression_evals",
            scenario_evals["case_count"] > 0 and scenario_evals["pass_rate"] == 1.0,
            "Configured scenarios pass their release regression set.",
            details={
                "case_count": scenario_evals["case_count"],
                "pass_rate": scenario_evals["pass_rate"],
            },
            severity="required",
        ),
        _check(
            "live_sap_connection",
            connector.mode == "live" and bool(connector.base_url),
            "A live SAP endpoint is configured.",
            details=connector.public_summary(),
            severity="external",
        ),
        _check(
            "named_user_authentication",
            connector.auth_type in {"principal_propagation", "oauth2_client_credentials"},
            "The connector uses enterprise OAuth or named-user propagation.",
            details={"auth_type": connector.auth_type},
            severity="external",
        ),
        _check(
            "safe_write_activation",
            connector.mode != "live" or connector.read_only or connector.shadow_writes,
            "Live connectors remain read-only or shadowed until release approval.",
            details={"read_only": connector.read_only, "shadow_writes": connector.shadow_writes},
            severity="required",
        ),
    ]
    required = [check for check in checks if check["severity"] == "required"]
    external = [check for check in checks if check["severity"] == "external"]
    return {
        "interview_demo_ready": all(check["passed"] for check in required),
        "production_ready": all(check["passed"] for check in checks),
        "required_pass_rate": round(sum(check["passed"] for check in required) / len(required), 4),
        "external_integration_pass_rate": round(
            sum(check["passed"] for check in external) / len(external), 4
        ),
        "checks": checks,
        "scenario_evals": scenario_evals,
        "next_actions": [
            check["remediation"]
            for check in checks
            if not check["passed"] and check.get("remediation")
        ],
    }


def _check(
    check_id: str,
    passed: bool,
    description: str,
    *,
    severity: str,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    remediations = {
        "policy_fail_closed": "Set default_action_effect to deny.",
        "tool_policy_coverage": "Add explicit Policy-as-Code entries for missing tool actions.",
        "erp_write_approval": "Require approval evidence for every ERP write tool.",
        "erp_write_idempotency": "Define stable idempotency fields for every ERP write tool.",
        "mcp_runtime_coverage": "Register an executable handler for every exposed ERP MCP tool.",
        "scenario_regression_evals": "Fix failed scenario evals before publishing.",
        "live_sap_connection": "Configure SAP_CONNECTOR_MODE=live and SAP_BASE_URL for a sandbox tenant.",
        "named_user_authentication": "Configure principal propagation or OAuth2 client credentials.",
        "safe_write_activation": "Re-enable read-only or shadow mode until production approval is recorded.",
    }
    return {
        "id": check_id,
        "passed": bool(passed),
        "status": "PASS" if passed else ("BLOCKED_EXTERNAL" if severity == "external" else "FAIL"),
        "severity": severity,
        "description": description,
        "details": details or {},
        "remediation": None if passed else remediations.get(check_id),
    }
