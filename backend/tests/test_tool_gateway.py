from app.agent.tool_gateway import (
    ToolExecutionContext,
    execute_tool,
    list_tool_specs,
)


class FakeTool:
    def __init__(self, result=None, error: Exception | None = None):
        self.result = result if result is not None else {"ok": True}
        self.error = error
        self.calls: list[dict] = []

    def invoke(self, args: dict):
        self.calls.append(args)
        if self.error:
            raise self.error
        return self.result


def test_registry_exposes_core_business_tools():
    names = {spec["name"] for spec in list_tool_specs()}

    assert {"lookup_order", "check_risk_level", "execute_refund", "send_notification"} <= names


def test_gateway_allows_read_tool_for_user_role():
    tool = FakeTool({"id": "123456"})

    result = execute_tool(
        "lookup_order",
        {"order_id": "123456"},
        context=ToolExecutionContext(actor_role="USER", requested_by_role="USER"),
        handler=tool,
    )

    assert result.success is True
    assert result.data == {"id": "123456"}
    assert result.audit_event["risk_level"] == "low"
    assert result.audit_event["policy"]["policy_version"] == "2026-05-20.v1"
    assert tool.calls == [{"order_id": "123456"}]


def test_gateway_blocks_direct_refund_for_user_role():
    tool = FakeTool({"success": True})

    result = execute_tool(
        "execute_refund",
        {"order_id": "123456", "amount": 299.0, "ticket_id": "42"},
        context=ToolExecutionContext(actor_role="USER", requested_by_role="USER"),
        handler=tool,
    )

    assert result.success is False
    assert result.authorized is False
    assert result.audit_event["authorized"] is False
    assert result.audit_event["policy"]["effect"] == "deny"
    assert tool.calls == []


def test_gateway_dry_run_does_not_call_side_effect_tool():
    tool = FakeTool({"success": True})

    result = execute_tool(
        "execute_refund",
        {"order_id": "123456", "amount": 299.0, "ticket_id": "42"},
        context=ToolExecutionContext(
            actor_role="AGENT",
            requested_by_role="USER",
            dry_run=True,
        ),
        handler=tool,
    )

    assert result.success is True
    assert result.dry_run is True
    assert result.data["dryRun"] is True
    assert result.data["sideEffect"] == "write"
    assert result.idempotency_key is not None
    assert tool.calls == []


def test_gateway_refund_idempotency_key_is_stable():
    args = {"order_id": "123456", "amount": 299.0, "ticket_id": "42"}
    context = ToolExecutionContext(actor_role="AGENT", requested_by_role="USER")

    first = execute_tool("execute_refund", args, context=context, handler=FakeTool())
    second = execute_tool("execute_refund", args, context=context, handler=FakeTool())

    assert first.success is True
    assert first.idempotency_key == second.idempotency_key
    assert first.audit_event["side_effect"] == "write"


def test_gateway_returns_structured_error_when_tool_fails():
    result = execute_tool(
        "send_notification",
        {"order_id": "123456", "refund_id": "REFUND_1"},
        context=ToolExecutionContext(actor_role="AGENT", requested_by_role="USER"),
        handler=FakeTool(error=RuntimeError("smtp timeout")),
    )

    assert result.success is False
    assert "smtp timeout" in result.error
    assert result.audit_event["success"] is False
    assert result.audit_event["risk_level"] == "medium"
