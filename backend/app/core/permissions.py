"""
工具权限矩阵

定义哪些角色可以触发哪些业务操作。
节点在执行前可调用 require_permission() 检查，
拒绝时抛出 PermissionDeniedError 而非默默跳过。
"""

from __future__ import annotations

from app.core.logging import get_logger

logger = get_logger(__name__)


class PermissionDeniedError(Exception):
    """角色无权执行该操作"""
    def __init__(self, role: str, action: str):
        self.role = role
        self.action = action
        super().__init__(f"角色 '{role}' 无权执行操作 '{action}'")


# 权限矩阵：action → 允许的角色集合（大写）
# 未列出的 action 默认所有角色均可执行
_PERMISSION_MATRIX: dict[str, set[str]] = {
    # 审批操作：仅限 MANAGER
    "approve_refund":     {"MANAGER"},
    "reject_refund":      {"MANAGER"},
    # 退款执行：AGENT 和 MANAGER（USER 自助走自动流程，不直接触发）
    "execute_refund":     {"AGENT", "MANAGER"},
    # 查询订单：所有登录角色
    "lookup_order":       {"USER", "AGENT", "MANAGER"},
    # 发起退款申请：所有角色（USER 是主要发起方）
    "submit_refund":      {"USER", "AGENT", "MANAGER"},
}


def check_permission(role: str, action: str) -> bool:
    """
    检查角色是否有权执行 action。
    未在矩阵中定义的 action 默认放行（宽松策略）。
    """
    allowed = _PERMISSION_MATRIX.get(action)
    if allowed is None:
        return True
    return role.upper() in allowed


def require_permission(role: str, action: str) -> None:
    """
    断言角色有权执行 action，无权时抛出 PermissionDeniedError 并记日志。
    """
    if not check_permission(role, action):
        logger.warning(
            "permission_denied",
            role=role,
            action=action,
            allowed=list(_PERMISSION_MATRIX.get(action, set())),
        )
        raise PermissionDeniedError(role=role, action=action)
    logger.debug("permission_granted", role=role, action=action)
