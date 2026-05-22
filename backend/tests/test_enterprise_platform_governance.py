import asyncio

from app.agent.approval_service import authorize_generic_approval
from app.agent.mcp_adapter import list_mcp_compatible_tools
from app.agent.saga import SagaStep, execute_saga, refund_saga_template
from app.agent.scenario_eval import run_scenario_eval
from app.agent.scenario_registry import get_default_registry
from app.agent.scenario_schema import RUNTIME_V2_SCHEMA
from app.agent.scenario_templates import instantiate_template, list_scenario_templates
from app.agent.scenario_versions import create_scenario_version, list_scenario_versions


def test_runtime_v2_schema_declares_required_platform_contract():
    assert RUNTIME_V2_SCHEMA["properties"]["schema_version"]["const"] == "2"
    assert "slot_extraction" in RUNTIME_V2_SCHEMA["required"]
    assert "tool" in RUNTIME_V2_SCHEMA["required"]
    assert "policy" in RUNTIME_V2_SCHEMA["required"]


def test_multilevel_approval_authorizes_stage_roles():
    manager = authorize_generic_approval(
        scenario_id="permission_request",
        approval_type="permission_request",
        action="approve",
        reviewer_role="MANAGER",
        stage_id="manager_review",
    )
    security = authorize_generic_approval(
        scenario_id="permission_request",
        approval_type="permission_request",
        action="approve",
        reviewer_role="SECURITY",
        stage_id="security_review",
    )
    wrong_stage = authorize_generic_approval(
        scenario_id="permission_request",
        approval_type="permission_request",
        action="approve",
        reviewer_role="MANAGER",
        stage_id="security_review",
    )

    assert manager.allowed is True
    assert manager.stage_id == "manager_review"
    assert security.allowed is True
    assert security.stage_id == "security_review"
    assert wrong_stage.allowed is False


def test_scenario_version_snapshots_are_file_backed(tmp_path, monkeypatch):
    import app.agent.scenario_versions as scenario_versions

    monkeypatch.setattr(scenario_versions, "VERSION_DIR", tmp_path)
    scenario = get_default_registry().get("permission_request")

    version = create_scenario_version(scenario, action="test", author="pytest", note="snapshot")
    second_version = create_scenario_version(scenario, action="test", author="pytest", note="snapshot")
    versions = list_scenario_versions("permission_request")

    assert version.version_id
    assert second_version.version_id != version.version_id
    assert versions[0].scenario_id == "permission_request"
    assert versions[0].action == "test"


def test_template_marketplace_instantiates_runtime_ready_scenario():
    template_ids = {template["id"] for template in list_scenario_templates()}
    scenario = instantiate_template("access_governance", "custom_access_governance")

    assert "access_governance" in template_ids
    assert scenario["id"] == "custom_access_governance"
    assert scenario["runtime"]["schema_version"] == "2"
    assert scenario["hitl"]["approval_chain"][0]["id"] == "manager_review"


def test_persistent_scenario_storage_can_seed_bundled_configs(tmp_path):
    from app.agent.scenario_registry import ensure_scenario_storage_initialized

    ensure_scenario_storage_initialized(tmp_path)

    assert (tmp_path / "refund.json").exists()
    assert (tmp_path / "permission_request.json").exists()
    assert (tmp_path / "reimbursement.json").exists()


def test_scenario_level_eval_runs_against_dry_run_runtime():
    result = asyncio.run(run_scenario_eval("reimbursement"))

    assert result["case_count"] == 1
    assert result["passed_count"] == 1
    assert result["pass_rate"] == 1.0


def test_mcp_adapter_exports_tool_gateway_metadata():
    descriptors = list_mcp_compatible_tools()
    names = {tool["name"] for tool in descriptors}
    refund = next(tool for tool in descriptors if tool["name"] == "execute_refund")

    assert "execute_refund" in names
    assert refund["annotations"]["destructiveHint"] is True
    assert refund["x-tool-gateway"]["riskLevel"] == "high"


def test_saga_executor_compensates_completed_steps_on_failure():
    events = []

    def first(context):
        events.append("first")
        return {"ok": True}

    def compensate_first(context):
        events.append("compensate_first")
        return {"compensated": True}

    def second(context):
        raise RuntimeError("external notification failed")

    execution = execute_saga(
        "test_saga",
        [
            SagaStep("first", first, compensate_first),
            SagaStep("second", second),
        ],
        {},
    )

    assert execution.success is False
    assert execution.failed_step == "second"
    assert execution.compensated_steps == ["first"]
    assert events == ["first", "compensate_first"]
    assert refund_saga_template()["saga_id"] == "refund_execution_saga"


def test_embedding_style_router_fallback_handles_semantic_access_text():
    match = get_default_registry().match("need temporary production database access")

    assert match.scenario_id == "permission_request"
    assert "fallback" in match.reason.lower() or match.matched_keywords
