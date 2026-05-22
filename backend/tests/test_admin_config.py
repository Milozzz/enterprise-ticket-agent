import asyncio

from app.api.routes.admin_config import (
    get_scenario_admin_config,
    simulate_scenario_runtime,
    simulate_scenario_route,
    validate_scenario_payload,
)
from app.api.routes.admin_config import (
    RouteSimulationPayload,
    RuntimeSimulationPayload,
    ScenarioConfigPayload,
)


def test_admin_config_catalog_exposes_platform_assets():
    catalog = asyncio.run(get_scenario_admin_config())

    scenario_ids = {scenario["id"] for scenario in catalog["scenarios"]}
    tool_names = {tool["name"] for tool in catalog["tools"]}
    workflow_names = {workflow["name"] for workflow in catalog["workflows"]}
    policy_names = {policy["name"] for policy in catalog["policies"]}

    assert {"refund", "permission_request", "reimbursement"} <= scenario_ids
    assert {"create_permission_request", "create_reimbursement_request"} <= tool_names
    assert {"refund_workflow", "permission_request_workflow", "reimbursement_workflow"} <= workflow_names
    assert {"refund_review", "permission_request_review", "reimbursement_review"} <= policy_names
    assert "MANAGER" in catalog["roles"]
    assert catalog["validation_summary"]["error_count"] == 0
    assert all(scenario["validation"]["valid"] for scenario in catalog["scenarios"])


def test_admin_route_simulation_uses_supervisor_registry():
    result = asyncio.run(
        simulate_scenario_route(
            RouteSimulationPayload(message="我要报销 1200 元差旅费，有发票")
        )
    )

    assert result["scenario_id"] == "reimbursement"
    assert result["workflow"] == "reimbursement_workflow"
    assert "报销" in result["matched_keywords"]


def test_admin_scenario_payload_validates_registered_workflow():
    payload = ScenarioConfigPayload(
        id="custom_access",
        name="Custom Access",
        description="Access governance workflow.",
        workflow="permission_request_workflow",
        owner="Security",
        business_domain="Access Governance",
        logic_module="app.agent.nodes.permission_request.permission_request_node",
        logic_file="backend/app/agent/nodes/permission_request.py",
        keywords=["申请权限", "access"],
        allowed_roles=["user", "AGENT"],
        tools=["create_permission_request"],
        policies=["permission_request_review"],
    )

    assert payload.allowed_roles == ["user", "AGENT"]
    assert payload.workflow == "permission_request_workflow"


def test_admin_validate_scenario_payload_returns_runtime_errors():
    payload = ScenarioConfigPayload(
        id="custom_access",
        name="Custom Access",
        description="Access governance workflow.",
        workflow="permission_request_workflow",
        owner="Security",
        business_domain="Access Governance",
        logic_module="app.agent.nodes.permission_request.permission_request_node",
        logic_file="backend/app/agent/nodes/permission_request.py",
        keywords=["access"],
        allowed_roles=["USER", "AGENT"],
        tools=["create_permission_request"],
        policies=["permission_request_review"],
    )

    result = asyncio.run(validate_scenario_payload(payload))

    assert result["validation"]["valid"] is False
    codes = {issue["code"] for issue in result["validation"]["issues"]}
    assert "runtime.required" in codes


def test_admin_runtime_simulation_runs_configured_scenario_in_dry_run():
    result = asyncio.run(
        simulate_scenario_runtime(
            "permission_request",
            RuntimeSimulationPayload(
                message="permission request GitHub admin access for production release",
                user_id="3",
                user_role="USER",
            ),
        )
    )

    runtime_result = result["result"]
    assert result["dry_run"] is True
    assert runtime_result["dry_run"] is True
    assert runtime_result["business_request"]["dryRun"] is True
    assert runtime_result["business_request"]["requestId"] == "DRY_RUN_PERMISSION_REQUEST"
    assert runtime_result["tool_gateway_events"][0]["dry_run"] is True
    assert any(event["type"] == "business_request_card" for event in runtime_result["ui_events"])
