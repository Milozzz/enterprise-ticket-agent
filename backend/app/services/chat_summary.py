from app.agent.utils import get_state_val


def build_summary(state) -> str:
    intent = get_state_val(state, "intent", "refund")
    if intent in ("query_order", "query_policy", "other", "permission_request", "reimbursement"):
        return ""
    if get_state_val(state, "refund_success"):
        return (
            "✅ 退款处理完成！\n\n"
            f"退款单号：{get_state_val(state, 'refund_id', 'N/A')}\n"
            f"退款金额：¥{get_state_val(state, 'order_amount', 0)}\n"
            "预计 3 个工作日内到账，财务已收到邮件通知。"
        )
    if get_state_val(state, "human_decision") == "reject":
        return "❌ 退款申请已被拒绝。如有疑问请联系客服。"
    if get_state_val(state, "requires_human_approval") and not get_state_val(state, "human_decision"):
        return "⏳ 退款金额超过风控阈值，请等待主管在上方面板审批。"
    if get_state_val(state, "error_message"):
        return f"⚠️ 处理遇到问题：{get_state_val(state, 'error_message')}\n请稍后重试。"
    return ""
