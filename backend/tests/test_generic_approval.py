from app.agent.approval_service import approval_action_name, authorize_generic_approval


def test_approval_action_name_uses_approval_type():
    assert approval_action_name("approve", "permission_request") == "approve_permission_request"
    assert approval_action_name("reject", "reimbursement") == "reject_reimbursement"


def test_manager_can_approve_permission_request():
    result = authorize_generic_approval(
        scenario_id="permission_request",
        approval_type="permission_request",
        action="approve",
        reviewer_role="MANAGER",
    )

    assert result.allowed is True
    assert result.policy_action == "approve_permission_request"
    assert result.review_roles == ("MANAGER", "SECURITY")
    assert result.policy_event["effect"] == "allow"


def test_agent_cannot_approve_permission_request():
    result = authorize_generic_approval(
        scenario_id="permission_request",
        approval_type="permission_request",
        action="approve",
        reviewer_role="AGENT",
    )

    assert result.allowed is False
    assert result.status_code == 403
    assert "scenario review roles" in result.reason


def test_manager_can_reject_reimbursement():
    result = authorize_generic_approval(
        scenario_id="reimbursement",
        approval_type="reimbursement",
        action="reject",
        reviewer_role="MANAGER",
    )

    assert result.allowed is True
    assert result.policy_action == "reject_reimbursement"
    assert result.review_roles == ("MANAGER", "FINANCE")
