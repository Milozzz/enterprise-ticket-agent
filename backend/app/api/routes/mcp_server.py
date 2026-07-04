"""MCP Streamable HTTP endpoint backed by the governed Tool Gateway."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import secrets
from typing import Annotated, Any, Mapping

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse

from app.agent.mcp_adapter import EXECUTABLE_MCP_TOOLS, list_executable_mcp_tools
from app.agent.tool_gateway import ToolExecutionContext, execute_erp_connector_tool_async
from app.core.auth import get_optional_user
from app.core.config import get_settings


router = APIRouter()
MCP_PROTOCOL_VERSION = "2025-06-18"
SUPPORTED_PROTOCOL_VERSIONS = {MCP_PROTOCOL_VERSION, "2025-03-26"}
_SESSIONS: dict[str, dict[str, Any]] = {}


async def _mcp_user(user: Annotated[dict | None, Depends(get_optional_user)]) -> dict:
    if user:
        return user
    if get_settings().environment == "development":
        return {"user_id": "local-mcp", "role": "AGENT"}
    raise HTTPException(status_code=401, detail="MCP authentication is required.")


@router.post("/mcp")
@router.post("/api/mcp", include_in_schema=False)
async def mcp_post(
    request: Request,
    user: Annotated[dict, Depends(_mcp_user)],
    session_id: Annotated[str | None, Header(alias="Mcp-Session-Id")] = None,
    protocol_version: Annotated[str | None, Header(alias="MCP-Protocol-Version")] = None,
    principal_token: Annotated[str | None, Header(alias="X-SAP-Principal-Token")] = None,
) -> Response:
    _validate_origin(request)
    try:
        message = await request.json()
    except Exception as exc:
        return _jsonrpc_error(None, -32700, "Parse error", str(exc))
    if not isinstance(message, Mapping):
        return _jsonrpc_error(None, -32600, "Invalid Request")
    if message.get("jsonrpc") != "2.0":
        return _jsonrpc_error(message.get("id"), -32600, "jsonrpc must be '2.0'")

    method = str(message.get("method") or "")
    request_id = message.get("id")
    if method == "initialize":
        params = dict(message.get("params") or {})
        requested_version = str(params.get("protocolVersion") or MCP_PROTOCOL_VERSION)
        negotiated = requested_version if requested_version in SUPPORTED_PROTOCOL_VERSIONS else MCP_PROTOCOL_VERSION
        new_session_id = secrets.token_urlsafe(32)
        _SESSIONS[new_session_id] = {
            "user_id": str(user.get("user_id") or "unknown"),
            "role": str(user.get("role") or "USER").upper(),
            "protocol_version": negotiated,
            "expires_at": datetime.now(timezone.utc) + timedelta(hours=8),
        }
        return JSONResponse(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "protocolVersion": negotiated,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {
                        "name": "enterprise-agent-tool-gateway",
                        "version": "1.0.0",
                    },
                    "instructions": (
                        "ERP write tools require approvalId metadata and are governed by read-only, "
                        "shadow, policy, idempotency, and audit controls."
                    ),
                },
            },
            headers={"Mcp-Session-Id": new_session_id},
        )

    session = _require_session(session_id, user)
    effective_version = protocol_version or str(session["protocol_version"])
    if effective_version not in SUPPORTED_PROTOCOL_VERSIONS:
        raise HTTPException(status_code=400, detail="Unsupported MCP protocol version.")

    if request_id is None:
        # Notifications have no JSON-RPC response body.
        return Response(status_code=status.HTTP_202_ACCEPTED)
    if method == "ping":
        return _jsonrpc_result(request_id, {})
    if method == "tools/list":
        return _jsonrpc_result(request_id, {"tools": list_executable_mcp_tools()})
    if method == "tools/call":
        return await _call_tool(
            request_id=request_id,
            params=dict(message.get("params") or {}),
            user=user,
            principal_token=principal_token,
        )
    return _jsonrpc_error(request_id, -32601, f"Method not found: {method}")


@router.get("/mcp")
@router.get("/api/mcp", include_in_schema=False)
async def mcp_get() -> Response:
    # This implementation uses JSON responses for request/response calls and
    # intentionally does not expose a standalone server notification stream.
    return Response(status_code=status.HTTP_405_METHOD_NOT_ALLOWED)


@router.delete("/mcp")
@router.delete("/api/mcp", include_in_schema=False)
async def mcp_delete(
    session_id: Annotated[str | None, Header(alias="Mcp-Session-Id")] = None,
) -> Response:
    if session_id:
        _SESSIONS.pop(session_id, None)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


async def _call_tool(
    *,
    request_id: Any,
    params: dict[str, Any],
    user: dict,
    principal_token: str | None,
) -> JSONResponse:
    name = str(params.get("name") or "")
    if name not in EXECUTABLE_MCP_TOOLS:
        return _jsonrpc_error(request_id, -32602, f"Unknown or non-executable tool: {name}")
    arguments = params.get("arguments") or {}
    if not isinstance(arguments, Mapping):
        return _jsonrpc_error(request_id, -32602, "Tool arguments must be an object.")
    metadata = params.get("_meta") or {}
    if not isinstance(metadata, Mapping):
        metadata = {}
    result = await execute_erp_connector_tool_async(
        name,
        dict(arguments),
        context=ToolExecutionContext(
            actor_role=str(user.get("role") or "USER").upper(),
            requested_by_role=str(user.get("role") or "USER").upper(),
            user_id=str(user.get("user_id") or "unknown"),
            thread_id=str(metadata.get("threadId") or ""),
            trace_id=str(metadata.get("traceId") or secrets.token_hex(12)),
            scenario=str(metadata.get("scenario") or "mcp_tool_call"),
            dry_run=bool(metadata.get("dryRun", False)),
            tenant_id=str(metadata.get("tenantId") or "default"),
            approval_id=str(metadata.get("approvalId")) if metadata.get("approvalId") else None,
            principal_token=principal_token,
        ),
    )
    structured = {
        "tool": result.tool_name,
        "success": result.success,
        "authorized": result.authorized,
        "data": result.data,
        "error": result.error,
        "durationMs": result.duration_ms,
        "idempotencyKey": result.idempotency_key,
    }
    return _jsonrpc_result(
        request_id,
        {
            "content": [{"type": "text", "text": json.dumps(structured, ensure_ascii=False, default=str)}],
            "structuredContent": structured,
            "isError": not result.success,
        },
    )


def _validate_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if not origin:
        return
    settings = get_settings()
    allowed = {
        settings.frontend_origin.rstrip("/"),
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    }
    if origin.rstrip("/") not in {item for item in allowed if item}:
        raise HTTPException(status_code=403, detail="MCP Origin is not allowed.")


def _require_session(session_id: str | None, user: dict) -> dict[str, Any]:
    if not session_id or session_id not in _SESSIONS:
        raise HTTPException(status_code=400, detail="A valid Mcp-Session-Id is required after initialize.")
    session = _SESSIONS[session_id]
    if session["expires_at"] <= datetime.now(timezone.utc):
        _SESSIONS.pop(session_id, None)
        raise HTTPException(status_code=404, detail="MCP session expired.")
    if session["user_id"] != str(user.get("user_id") or "unknown"):
        raise HTTPException(status_code=403, detail="MCP session identity mismatch.")
    return session


def _jsonrpc_result(request_id: Any, result: Any) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": request_id, "result": result})


def _jsonrpc_error(request_id: Any, code: int, message: str, data: Any = None) -> JSONResponse:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return JSONResponse({"jsonrpc": "2.0", "id": request_id, "error": error})
