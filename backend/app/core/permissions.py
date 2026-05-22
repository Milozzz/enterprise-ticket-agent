"""Permission facade backed by Policy-as-Code."""

from __future__ import annotations

import logging

from app.core.policy import allowed_roles_for_action, evaluate_action_policy

logger = logging.getLogger(__name__)


class PermissionDeniedError(Exception):
    """Raised when a role is not allowed to execute an action."""

    def __init__(self, role: str, action: str):
        self.role = role
        self.action = action
        super().__init__(f"角色 '{role}' 无权执行操作 '{action}'")


def check_permission(role: str, action: str) -> bool:
    """Return whether the role can execute the action under current policy."""
    return evaluate_action_policy(role, action).allowed


def require_permission(role: str, action: str) -> None:
    """Assert that the role can execute the action, otherwise raise."""
    decision = evaluate_action_policy(role, action)
    if not decision.allowed:
        logger.warning(
            "permission_denied role=%s action=%s allowed=%s policy_version=%s",
            role,
            action,
            sorted(allowed_roles_for_action(action) or set()),
            decision.policy_version,
        )
        raise PermissionDeniedError(role=role, action=action)
    logger.debug(
        "permission_granted role=%s action=%s policy_version=%s",
        role,
        action,
        decision.policy_version,
    )
