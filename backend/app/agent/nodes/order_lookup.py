"""
节点2：订单查询
通过 Tool Gateway 从 canonical store 或实时 ERP Connector 获取订单。
"""

from decimal import Decimal, InvalidOperation
from typing import Any

from app.agent.state import AgentState
from app.agent.utils import get_state_val
from app.agent.tool_gateway import (
    execute_erp_connector_tool_async,
    execute_tool_async,
    gateway_context_from_state,
)
from app.agent.tools.order_tools import _get_order_detail_async
from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


def _selected_connector_id(state: AgentState) -> str:
    explicit = str(get_state_val(state, "connector_id", "") or "").strip()
    if explicit:
        return explicit
    settings = get_settings()
    if str(settings.sap_connector_mode).lower() == "live":
        return str(settings.sap_connector_id or "CONN-SAP-ODATA-DEMO")
    return "CONN-MOCK-ERP"


def _money(value: Any) -> float | None:
    try:
        return float(Decimal(str(value)).quantize(Decimal("0.01")))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _normalize_connector_order(
    connector_snapshot: dict[str, Any],
    *,
    requested_order_id: str,
    local_projection: dict[str, Any] | None,
    user_id: str,
) -> dict[str, Any]:
    payload: Any = connector_snapshot.get("data")
    if isinstance(payload, list):
        payload = payload[0] if payload else {}
    if not isinstance(payload, dict):
        payload = {}

    mode = str(connector_snapshot.get("mode") or "live").lower()
    amount = _money(payload.get("amount") or payload.get("TotalNetAmount"))
    if amount is None and mode == "mock" and local_projection:
        amount = _money(local_projection.get("totalAmount"))
    if amount is None:
        return {"error": "ERP 订单响应缺少有效金额，已阻止后续财务操作"}

    local = dict(local_projection or {})
    canonical_id = str(
        payload.get("orderId")
        or payload.get("SalesOrder")
        or local.get("canonicalId")
        or requested_order_id
    )
    return {
        **local,
        "id": requested_order_id,
        "canonicalId": canonical_id,
        "sourceSystem": "SAP" if mode == "live" else str(payload.get("sourceSystem") or "MINI_ERP"),
        "userId": str(local.get("userId") or user_id),
        # Connector values are authoritative for fields used by risk and writes.
        "status": str(payload.get("status") or local.get("status") or "unknown"),
        "totalAmount": amount,
        "currency": str(payload.get("currency") or local.get("currency") or "CNY").upper(),
        "connectorSnapshot": {
            "connectorId": connector_snapshot.get("connectorId"),
            "mode": connector_snapshot.get("mode"),
            "requestId": connector_snapshot.get("requestId"),
            "remoteRequestId": connector_snapshot.get("remoteRequestId"),
            "etag": connector_snapshot.get("etag"),
            "durationMs": connector_snapshot.get("durationMs"),
        },
    }


async def _lookup_order(state: AgentState, order_id: str):
    context = gateway_context_from_state(state, actor_role="AGENT", scenario="refund")
    connector_id = _selected_connector_id(state)
    tenant_id = context.tenant_id

    if connector_id != "CONN-MOCK-ERP":
        connector_result = await execute_erp_connector_tool_async(
            "erp_get_order",
            {"order_id": order_id, "connector_id": connector_id},
            context=context,
        )
        if not connector_result.success:
            return None, connector_id, [connector_result.audit_event], connector_result.error

        # Local data is enrichment only in live mode. Amount/status/currency stay
        # authoritative from SAP so stale projections cannot authorize a write.
        try:
            local_projection = await _get_order_detail_async(order_id, tenant_id=tenant_id)
            if "error" in local_projection:
                local_projection = None
        except Exception as exc:
            # The canonical projection enriches UI/risk context but must not
            # make an authoritative SAP read unavailable.
            logger.warning(
                "canonical_order_projection_unavailable",
                order_id=order_id,
                tenant_id=tenant_id,
                error=str(exc),
            )
            local_projection = None
        order_data = _normalize_connector_order(
            dict(connector_result.data or {}),
            requested_order_id=order_id,
            local_projection=local_projection,
            user_id=context.user_id,
        )
        return order_data, connector_id, [connector_result.audit_event], None

    async def canonical_handler(**values: Any) -> dict[str, Any]:
        return await _get_order_detail_async(
            str(values["order_id"]),
            tenant_id=tenant_id,
        )

    gateway_result = await execute_tool_async(
        "lookup_order",
        {"order_id": order_id},
        context=context,
        handler=canonical_handler,
    )
    return (
        gateway_result.data if gateway_result.success else None,
        connector_id,
        [gateway_result.audit_event],
        gateway_result.error,
    )


async def lookup_order_node(state: AgentState) -> dict:
    """
    订单查询节点
    调用工具函数查询订单，并生成 OrderCard UI 组件渲染指令
    """
    order_id = get_state_val(state, "order_id")
    logger.info("node_start", node="lookup_order", order_id=order_id)

    ui_thinking = {
        "type": "thinking_stream",
        "data": {
            "steps": [
                {
                    "step": "looking_up_order",
                    "label": "查询订单",
                    "status": "running",
                    "detail": f"正在查询订单 #{order_id}...",
                }
            ]
        },
    }

    if not order_id:
        return {
            "error_message": "未找到订单号，请提供有效的订单号",
            "current_step": "lookup_order_error",
            "ui_events": [ui_thinking],
        }

    try:
        logger.info("invoking_get_order_detail", order_id=order_id)
        order_data, connector_id, audit_events, gateway_error = await _lookup_order(state, order_id)
        if order_data is None:
            raise RuntimeError(gateway_error or "lookup_order failed")
        logger.info("tool_output", data=order_data)

        if not order_data or "error" in order_data:
            raw_msg = order_data.get("error") if order_data else "订单查询返回空数据"
            ui_thinking["data"]["steps"][0]["status"] = "done"
            ui_thinking["data"]["steps"][0]["detail"] = f"未找到订单 #{order_id}"
            return {
                "error_message": f"未找到订单 #{order_id}，请确认订单号是否正确（原因：{raw_msg}）",
                "reply_text": f"未找到订单 **#{order_id}**，请确认订单号是否正确。\n\n您可以尝试：「订单号 789012 申请退款，质量问题」",
                "current_step": "lookup_order_error",
                "tool_gateway_events": audit_events,
                "ui_events": [ui_thinking],
            }

        # 更新思考流状态
        ui_thinking["data"]["steps"][0]["status"] = "done"
        ui_thinking["data"]["steps"][0]["detail"] = (
            f"订单金额：¥{order_data.get('totalAmount')}，状态：{order_data.get('status')}"
        )

        # 生成 OrderCard 渲染指令
        ui_order_card = {
            "type": "order_card",
            "data": order_data,
        }

        erp_context = order_data.get("erpContext") or {}
        open_items = erp_context.get("openItems") or []
        open_item = next(
            (
                item
                for item in open_items
                if not item.get("clearing_document_id")
                and not item.get("clearingDocument")
            ),
            {},
        )
        canonical_order_id = order_data.get("canonicalId", order_id)

        return {
            "order_id": canonical_order_id,
            "order_detail": order_data,
            "order_amount": order_data.get("totalAmount", 0.0),
            "currency": str(order_data.get("currency") or "CNY").upper(),
            "open_item_id": str(
                open_item.get("open_item_id")
                or open_item.get("openItemId")
                or f"OI-AR-{canonical_order_id}"
            ),
            "connector_id": connector_id,
            "user_id": order_data.get("userId", get_state_val(state, "user_id", "unknown")),
            "current_step": "lookup_order_done",
            "tool_gateway_events": audit_events,
            "ui_events": [ui_thinking, ui_order_card],
        }

    except Exception as e:
        logger.error("lookup_order_error", error=str(e), order_id=order_id)
        import traceback
        traceback.print_exc()
        return {
            "error_message": f"订单查询失败: {str(e)}",
            "current_step": "lookup_order_error",
            "ui_events": [ui_thinking],
        }
