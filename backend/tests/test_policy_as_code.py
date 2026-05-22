from app.core.policy import (
    evaluate_action_policy,
    evaluate_refund_review_policy,
    get_policy_version,
    load_policy,
)
from app.core.permissions import check_permission, require_permission, PermissionDeniedError


def test_policy_file_loads_with_version_and_actions():
    policy = load_policy()

    assert get_policy_version() == "2026-05-20.v1"
    assert "execute_refund" in policy["actions"]
    assert policy["actions"]["execute_refund"]["allowed_roles"] == ["AGENT", "MANAGER"]


def test_action_policy_allows_manager_approval():
    decision = evaluate_action_policy("MANAGER", "approve_refund")

    assert decision.allowed is True
    assert decision.effect == "allow"
    assert decision.policy_version == "2026-05-20.v1"
    assert decision.matched_rules == ("actions.approve_refund.allowed_roles",)


def test_action_policy_allows_generic_scenario_approval_roles():
    permission_decision = evaluate_action_policy("SECURITY", "approve_permission_request")
    reimbursement_decision = evaluate_action_policy("FINANCE", "reject_reimbursement")

    assert permission_decision.allowed is True
    assert reimbursement_decision.allowed is True


def test_action_policy_denies_user_direct_refund_execution():
    decision = evaluate_action_policy("USER", "execute_refund")

    assert decision.allowed is False
    assert decision.effect == "deny"
    assert "not allowed" in decision.reason


def test_permissions_facade_uses_policy_as_code():
    assert check_permission("AGENT", "execute_refund") is True
    assert check_permission("USER", "execute_refund") is False

    try:
        require_permission("USER", "execute_refund")
    except PermissionDeniedError as exc:
        assert exc.role == "USER"
        assert exc.action == "execute_refund"
    else:
        raise AssertionError("require_permission should deny USER execute_refund")


def test_unknown_action_keeps_default_allow_policy():
    decision = evaluate_action_policy("USER", "some_undefined_action")

    assert decision.allowed is True
    assert decision.matched_rules == ("default_action_effect",)


def test_refund_policy_requires_human_review_for_amount_threshold():
    decision = evaluate_refund_review_policy(
        amount=501,
        risk_score=10,
        risk_level="low",
        user_history={},
    )

    assert decision.requires_human_review is True
    assert "refund.amount.requires_human_review" in decision.matched_rules


def test_refund_policy_requires_human_review_for_risk_score():
    decision = evaluate_refund_review_policy(
        amount=99,
        risk_score=40,
        risk_level="medium",
        user_history={},
    )

    assert decision.requires_human_review is True
    assert "refund.risk_score.requires_human_review" in decision.matched_rules


def test_refund_policy_requires_human_review_for_fraud_flag():
    decision = evaluate_refund_review_policy(
        amount=99,
        risk_score=0,
        risk_level="low",
        user_history={"fraud_flag": True},
    )

    assert decision.requires_human_review is True
    assert "refund.fraud_flag.requires_human_review" in decision.matched_rules


def test_refund_policy_allows_low_risk_auto_approval():
    decision = evaluate_refund_review_policy(
        amount=99,
        risk_score=0,
        risk_level="low",
        user_history={},
    )

    assert decision.requires_human_review is False
    assert decision.effect == "allow_auto_approval"
    assert decision.matched_rules == ()
