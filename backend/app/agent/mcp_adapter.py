"""MCP-compatible descriptors for Tool Gateway tools.

This is not a full MCP server. It is an adapter layer that exposes the same
tool metadata shape the platform would need to register its internal Tool
Gateway tools behind MCP later.
"""

from __future__ import annotations

from typing import Any

from app.agent.tool_gateway import TOOL_SPECS, ToolSideEffect


def list_mcp_compatible_tools() -> list[dict[str, Any]]:
    return [tool_spec_to_mcp_descriptor(name) for name in sorted(TOOL_SPECS)]


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
        "inputSchema": {
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
            "idempotencyFields": list(spec.idempotency_fields),
            "idempotencyNamespace": spec.idempotency_namespace,
        },
    }
