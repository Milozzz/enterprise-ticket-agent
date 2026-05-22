import asyncio

from langchain_core.messages import HumanMessage

from app.agent.generic_runtime import extract_slots, run_configured_scenario
from app.agent.scenario_registry import get_default_registry


def test_permission_runtime_config_declares_core_contract():
    scenario = get_default_registry().get("permission_request")

    assert scenario.runtime["schema_version"] == "2"
    assert scenario.runtime["tool"]["name"] == "create_permission_request"
    assert scenario.runtime["policy"]["name"] == "permission_request_review"
    assert "business_request_card" in scenario.runtime["ui"]


def test_runtime_slot_extraction_uses_configured_keywords():
    runtime = get_default_registry().get("permission_request").runtime

    slots = extract_slots(
        "permission request GitHub admin access for release deployment",
        runtime["slot_extraction"],
    )

    assert slots["system"] == "GitHub"
    assert slots["permission_level"] == "admin"
    assert "GitHub admin" in slots["reason"]


def test_runtime_executes_permission_request_without_scenario_specific_node_logic():
    state = {
        "messages": [HumanMessage(content="permission request GitHub admin access for release deployment")],
        "user_role": "USER",
        "user_id": "3",
        "thread_id": "runtime-thread-1",
        "trace_id": "runtime-trace-1",
    }

    result = asyncio.run(run_configured_scenario(state, "permission_request"))

    assert result["intent"] == "permission_request"
    assert result["permission_system"] == "GitHub"
    assert result["permission_level"] == "admin"
    assert result["business_request"]["requestId"].startswith("ACCESS_REQ_")
    assert result["approval_required"] is True
    assert any(event["type"] == "business_request_card" for event in result["ui_events"])


def test_runtime_executes_reimbursement_from_configured_amount_and_category():
    state = {
        "messages": [HumanMessage(content="reimbursement 1200 travel expense invoice")],
        "user_role": "USER",
        "user_id": "3",
        "thread_id": "runtime-thread-2",
        "trace_id": "runtime-trace-2",
    }

    result = asyncio.run(run_configured_scenario(state, "reimbursement"))

    assert result["intent"] == "reimbursement"
    assert result["reimbursement_amount"] == 1200
    assert result["reimbursement_category"] == "travel"
    assert result["business_request"]["requestId"].startswith("EXPENSE_REQ_")
    assert result["approval_required"] is True
