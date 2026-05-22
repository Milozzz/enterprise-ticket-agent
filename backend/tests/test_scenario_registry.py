from app.agent.scenario_registry import get_default_registry, list_scenarios
from app.agent.workflow_factory import resolve_workflow_entry, resolve_workflow_name


def test_scenario_registry_loads_configured_scenarios():
    scenario_ids = {scenario["id"] for scenario in list_scenarios()}

    assert {"refund", "permission_request", "reimbursement"} <= scenario_ids


def test_registry_routes_permission_request_by_keywords():
    match = get_default_registry().match("帮我申请 GitHub 管理员权限，用于处理 CI 配置")

    assert match.scenario_id == "permission_request"
    assert match.workflow == "permission_request_workflow"
    assert "申请权限" in match.matched_keywords or "管理员权限" in match.matched_keywords


def test_registry_routes_reimbursement_by_keywords():
    match = get_default_registry().match("我要报销 1200 元差旅费，有发票")

    assert match.scenario_id == "reimbursement"
    assert match.workflow == "reimbursement_workflow"
    assert "报销" in match.matched_keywords


def test_registry_falls_back_to_refund_general_support():
    match = get_default_registry().match("你好，帮我看一下这个问题")

    assert match.scenario_id == "refund"
    assert match.workflow == "refund_workflow"
    assert match.confidence < 0.5


def test_registry_semantic_fallback_ignores_generic_stopwords():
    match = get_default_registry().match("hello can you help me with a question")

    assert match.scenario_id == "refund"
    assert match.workflow == "refund_workflow"
    assert match.confidence < 0.5


def test_workflow_factory_resolves_entries_from_scenario_config():
    assert resolve_workflow_name("permission_request") == "permission_request_workflow"
    assert resolve_workflow_entry("permission_request") == "permission_request"
    assert resolve_workflow_entry("reimbursement") == "reimbursement"
    assert resolve_workflow_entry("refund") == "classify_intent"
