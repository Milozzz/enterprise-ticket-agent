"""MCP descriptors shared by Tool Gateway and the Streamable HTTP server."""

from __future__ import annotations

from typing import Any

from app.agent.tool_gateway import TOOL_SPECS, ToolSideEffect


EXECUTABLE_MCP_TOOLS = frozenset(
    {
        "erp_get_order",
        "erp_query_doctype",
        "erp_create_credit_memo",
        "erp_clear_open_item",
        "erp_reverse_document",
    }
)


def list_mcp_compatible_tools() -> list[dict[str, Any]]:
    return [tool_spec_to_mcp_descriptor(name) for name in sorted(TOOL_SPECS)]


def list_executable_mcp_tools() -> list[dict[str, Any]]:
    """Return only tools backed by a runtime handler in the MCP server."""

    return [tool_spec_to_mcp_descriptor(name) for name in sorted(EXECUTABLE_MCP_TOOLS)]


def tool_spec_to_mcp_descriptor(tool_name: str) -> dict[str, Any]:
    spec = TOOL_SPECS[tool_name]
    return {
        "name": spec.name,
        "description": spec.description,
        "annotations": {
            "title": spec.name.replace("_", " ").title(),
            "readOnlyHint": spec.side_effect == ToolSideEffect.READ,
            "destructiveHint": spec.side_effect in {ToolSideEffect.WRITE, ToolSideEffect.EXTERNAL},
            "idempotentHint": bool(spec.idempotency_fields),
            "openWorldHint": spec.side_effect == ToolSideEffect.EXTERNAL,
        },
        "inputSchema": spec.input_schema or {
            "type": "object",
            "additionalProperties": True,
            "properties": {
                field: {"type": "string", "description": f"Idempotency field: {field}"}
                for field in spec.idempotency_fields
            },
        },
        "x-tool-gateway": {
            "action": spec.action,
            "riskLevel": spec.risk_level.value,
            "sideEffect": spec.side_effect.value,
            "category": spec.category,
            "owner": spec.owner,
            "timeoutSeconds": spec.timeout_seconds,
            "retryLimit": spec.retry_limit,
            "idempotencyFields": list(spec.idempotency_fields),
            "idempotencyNamespace": spec.idempotency_namespace,
        },
    }
