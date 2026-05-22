import asyncio

from langchain_core.messages import HumanMessage

from app.agent.nodes.permission_request import permission_request_node
from app.agent.nodes.reimbursement import reimbursement_node
from app.agent.nodes.supervisor import supervisor_router_node
from app.agent.tool_gateway import ToolExecutionContext, execute_tool, list_tool_specs
from app.agent.tools.enterprise_tools import create_permission_request
from app.core.policy import (
    evaluate_permission_request_policy,
    evaluate_reimbursement_policy,
)


def test_supervisor_routes_permission_request():
    state = {
        "messages": [HumanMessage(content="帮我申请 GitHub 管理员权限，用于发布配置")],
        "user_role": "USER",
        "user_id": "3",
    }

    result = asyncio.run(supervisor_router_node(state))

    assert result["scenario_id"] == "permission_request"
    assert result["supervisor_decision"]["workflow"] == "permission_request_workflow"
    assert result["supervisor_decision"]["hitl_enabled"] is True


def test_supervisor_routes_reimbursement_request():
    state = {
        "messages": [HumanMessage(content="我要报销 1200 元差旅费，有发票")],
        "user_role": "USER",
        "user_id": "3",
    }

    result = asyncio.run(supervisor_router_node(state))

    assert result["scenario_id"] == "reimbursement"
    assert result["supervisor_decision"]["workflow"] == "reimbursement_workflow"


def test_permission_request_policy_requires_human_for_admin_access():
    decision = evaluate_permission_request_policy(system="GitHub", permission_level="admin")

    assert decision.requires_human_review is True
    assert "permission.admin.requires_human_review" in decision.matched_rules


def test_reimbursement_policy_requires_human_for_large_amount():
    decision = evaluate_reimbursement_policy(amount=1200, category="travel")

    assert decision.requires_human_review is True
    assert "reimbursement.amount.requires_human_review" in decision.matched_rules


def test_tool_gateway_exposes_enterprise_scenario_tools():
    names = {spec["name"] for spec in list_tool_specs()}

    assert "create_permission_request" in names
    assert "create_reimbursement_request" in names


def test_tool_gateway_blocks_user_direct_permission_write():
    result = execute_tool(
        "create_permission_request",
        {
            "system": "GitHub",
            "permission_level": "admin",
            "reason": "deploy production config",
            "requester_id": "3",
        },
        context=ToolExecutionContext(actor_role="USER", requested_by_role="USER"),
        handler=create_permission_request,
    )

    assert result.success is False
    assert result.authorized is False


def test_permission_request_node_creates_business_request_with_policy_event():
    state = {
        "messages": [HumanMessage(content="帮我申请 GitHub 管理员权限，用于发布配置")],
        "user_role": "USER",
        "user_id": "3",
        "thread_id": "thread-1",
        "trace_id": "trace-1",
    }

    result = asyncio.run(permission_request_node(state))

    assert result["intent"] == "permission_request"
    assert result["approval_required"] is True
    assert result["business_request"]["requestId"].startswith("ACCESS_REQ_")
    assert result["tool_gateway_events"][0]["scenario"] == "permission_request"
    assert "权限申请已创建" in result["reply_text"]
    ui_types = [event["type"] for event in result["ui_events"]]
    assert "business_request_card" in ui_types
    assert "generic_approval_panel" in ui_types
    card = next(event for event in result["ui_events"] if event["type"] == "business_request_card")
    approval = next(event for event in result["ui_events"] if event["type"] == "generic_approval_panel")
    assert card["data"]["scenarioId"] == "permission_request"
    assert card["data"]["policy"]["requiresHumanReview"] is True
    assert approval["data"]["reviewRoles"] == ["MANAGER", "SECURITY"]
    assert approval["data"]["threadId"] == "thread-1"


def test_reimbursement_node_creates_business_request_with_policy_event():
    state = {
        "messages": [HumanMessage(content="我要报销 1200 元差旅费，有发票")],
        "user_role": "USER",
        "user_id": "3",
        "thread_id": "thread-1",
        "trace_id": "trace-1",
    }

    result = asyncio.run(reimbursement_node(state))

    assert result["intent"] == "reimbursement"
    assert result["approval_required"] is True
    assert result["business_request"]["requestId"].startswith("EXPENSE_REQ_")
    assert result["reimbursement_amount"] == 1200
    assert "报销申请已创建" in result["reply_text"]
    ui_types = [event["type"] for event in result["ui_events"]]
    assert "business_request_card" in ui_types
    assert "generic_approval_panel" in ui_types
    card = next(event for event in result["ui_events"] if event["type"] == "business_request_card")
    approval = next(event for event in result["ui_events"] if event["type"] == "generic_approval_panel")
    assert card["data"]["scenarioId"] == "reimbursement"
    assert card["data"]["policy"]["requiresHumanReview"] is True
    assert approval["data"]["reviewRoles"] == ["MANAGER", "FINANCE"]
    assert approval["data"]["threadId"] == "thread-1"
