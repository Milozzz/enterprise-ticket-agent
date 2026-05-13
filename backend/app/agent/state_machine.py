"""
退款业务状态机

定义显式的 RefundState 枚举和合法转移表。
每次状态变更都经过 transition() 校验，非法跳转立即抛错并写审计。

状态流转图：
  CREATED → CLASSIFIED → ORDER_LOADED → RISK_EVALUATED
                                              ├── (低风险) → APPROVED → REFUNDED → COMPLETED
                                              └── (高风险) → PENDING_APPROVAL
                                                                ├── approve → APPROVED → REFUNDED → COMPLETED
                                                                └── reject  → REJECTED
  任意状态 → FAILED（异常降级）
"""

from enum import Enum


class RefundState(str, Enum):
    CREATED           = "CREATED"           # 用户发起请求
    CLASSIFIED        = "CLASSIFIED"        # 意图识别完成
    ORDER_LOADED      = "ORDER_LOADED"      # 订单信息已查询
    RISK_EVALUATED    = "RISK_EVALUATED"    # 风控评分完成
    PENDING_APPROVAL  = "PENDING_APPROVAL"  # 等待人工审批
    APPROVED          = "APPROVED"          # 审批通过（含自动审批）
    REFUNDED          = "REFUNDED"          # 退款已执行
    COMPLETED         = "COMPLETED"         # 通知已发送，流程结束
    REJECTED          = "REJECTED"          # 退款被拒绝
    FAILED            = "FAILED"            # 异常终止


# 合法的状态转移表：每个状态允许跳转到哪些下一状态
_ALLOWED_TRANSITIONS: dict[RefundState, set[RefundState]] = {
    RefundState.CREATED:          {RefundState.CLASSIFIED,       RefundState.FAILED},
    RefundState.CLASSIFIED:       {RefundState.ORDER_LOADED,     RefundState.FAILED},
    RefundState.ORDER_LOADED:     {RefundState.RISK_EVALUATED,   RefundState.FAILED},
    RefundState.RISK_EVALUATED:   {RefundState.APPROVED,         RefundState.PENDING_APPROVAL, RefundState.FAILED},
    RefundState.PENDING_APPROVAL: {RefundState.APPROVED,         RefundState.REJECTED,         RefundState.FAILED},
    RefundState.APPROVED:         {RefundState.REFUNDED,         RefundState.FAILED},
    RefundState.REFUNDED:         {RefundState.COMPLETED,        RefundState.FAILED},
    # 终态：不允许任何转移
    RefundState.COMPLETED:        set(),
    RefundState.REJECTED:         set(),
    RefundState.FAILED:           set(),
}


class InvalidStateTransitionError(Exception):
    """非法状态转移异常"""
    def __init__(self, from_state: RefundState, to_state: RefundState):
        self.from_state = from_state
        self.to_state = to_state
        super().__init__(
            f"非法状态转移：{from_state.value} → {to_state.value}，"
            f"允许的下一状态：{[s.value for s in _ALLOWED_TRANSITIONS.get(from_state, set())]}"
        )


def transition(current: RefundState, next_state: RefundState) -> RefundState:
    """
    校验并执行状态转移。

    Args:
        current:    当前状态
        next_state: 目标状态

    Returns:
        next_state（转移合法时）

    Raises:
        InvalidStateTransitionError: 转移不合法时
    """
    allowed = _ALLOWED_TRANSITIONS.get(current, set())
    if next_state not in allowed:
        raise InvalidStateTransitionError(current, next_state)
    return next_state


def get_allowed_next(current: RefundState) -> list[RefundState]:
    """返回当前状态允许的所有下一状态"""
    return list(_ALLOWED_TRANSITIONS.get(current, set()))
