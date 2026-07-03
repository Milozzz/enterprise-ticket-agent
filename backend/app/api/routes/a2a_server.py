"""A2A 0.3 JSON-RPC endpoint for SAP Joule Studio BYOA integration."""

from __future__ import annotations

import secrets
from typing import Annotated, Any, Mapping

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from app.agent.a2a_tasks import (
    TERMINAL_STATES,
    cancel_owned_task,
    create_or_resume_task,
    execute_task,
    get_owned_task,
    message_text,
    task_to_a2a,
    validate_callback_url,
)
from app.core.auth import get_optional_user
from app.core.config import get_settings
from app.db.database import AsyncSessionLocal
from app.db.tenant_context import tenant_scope


router = APIRouter()


async def _a2a_user(user: Annotated[dict | None, Depends(get_optional_user)]) -> dict:
    if user:
        return user
    settings = get_settings()
    if settings.environment == "development":
        return {
            "user_id": "local-a2a",
            "role": "AGENT",
            "tenant_id": settings.default_tenant_id,
        }
    raise HTTPException(status_code=401, detail="A2A authentication is required.")


@router.get("/.well-known/agent-card.json")
async def get_agent_card() -> dict[str, Any]:
    base_url = get_settings().agent_public_url.rstrip("/")
    return {
        "name": "Enterprise High-Risk Workflow Agent",
        "description": (
            "Governed enterprise agent for refunds, reimbursement, access requests, "
            "policy decisions, and ERP financial execution."
        ),
        "url": f"{base_url}/a2a",
        "version": "1.1.0",
        "protocolVersion": "0.3.0",
        "capabilities": {
            "streaming": False,
            "pushNotifications": True,
            "stateTransitionHistory": True,
        },
        "securitySchemes": {
            "bearer": {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"}
        },
        "security": [{"bearer": []}],
        "defaultInputModes": ["text", "application/json"],
        "defaultOutputModes": ["text", "application/json"],
        "skills": [
            {
                "id": "refund_finance_workflow",
                "name": "Refund Finance Workflow",
                "description": "Analyze and safely coordinate return-to-refund processing.",
                "tags": ["refund", "credit-memo", "HITL", "Saga"],
                "examples": ["Process refund for order ERP-ORD-1002"],
            },
            {
                "id": "permission_request",
                "name": "Enterprise Access Request",
                "description": "Create policy-governed permission requests.",
                "tags": ["access", "RBAC", "approval"],
                "examples": ["Request production database read access"],
            },
            {
                "id": "reimbursement",
                "name": "Employee Reimbursement",
                "description": "Validate and route reimbursement requests.",
                "tags": ["expense", "finance", "approval"],
                "examples": ["Submit a 1200 CNY travel reimbursement"],
            },
        ],
    }


@router.post("/a2a")
async def a2a_rpc(
    payload: dict[str, Any],
    user: Annotated[dict, Depends(_a2a_user)],
) -> JSONResponse:
    request_id = payload.get("id")
    if payload.get("jsonrpc") != "2.0":
        return _error(request_id, -32600, "jsonrpc must be '2.0'")
    method = str(payload.get("method") or "")
    params = payload.get("params") or {}
    if not isinstance(params, Mapping):
        return _error(request_id, -32602, "params must be an object")
    if method == "message/send":
        return await _send_message(request_id, dict(params), user)
    if method == "tasks/get":
        return await _get_task(request_id, dict(params), user)
    if method == "tasks/cancel":
        return await _cancel_task(request_id, dict(params), user)
    if method == "tasks/pushNotificationConfig/set":
        return await _set_push_config(request_id, dict(params), user)
    if method == "tasks/pushNotificationConfig/get":
        return await _get_push_config(request_id, dict(params), user)
    return _error(request_id, -32601, f"Method not found: {method}")


async def _send_message(request_id: Any, params: dict[str, Any], user: dict) -> JSONResponse:
    message = params.get("message") or {}
    if not isinstance(message, Mapping):
        return _error(request_id, -32602, "message must be an object")
    normalized_message = dict(message)
    if not message_text(normalized_message):
        return _error(request_id, -32602, "message must contain a text part")
    task_id = str(params.get("id") or params.get("taskId") or secrets.token_hex(16))
    context_id = str(
        message.get("contextId")
        or params.get("contextId")
        or secrets.token_hex(12)
    )
    configurations = params.get("configurations") or {}
    if not isinstance(configurations, Mapping):
        return _error(request_id, -32602, "configurations must be an object")
    push_config = params.get("pushNotificationConfig") or configurations.get(
        "pushNotificationConfig"
    ) or {}
    if not isinstance(push_config, Mapping):
        return _error(request_id, -32602, "pushNotificationConfig must be an object")
    push_notifications = bool(configurations.get("pushNotifications"))
    callback_url = str(
        push_config.get("url") or get_settings().a2a_default_callback_url or ""
    ).strip() or None
    if callback_url:
        push_notifications = True
    if push_notifications and not callback_url:
        return _error(request_id, -32602, "push notification callback URL is required")
    try:
        callback_url = validate_callback_url(callback_url)
    except ValueError as exc:
        return _error(request_id, -32602, str(exc))

    tenant_id = _tenant(user)
    owner_user_id = _owner(user)
    with tenant_scope(tenant_id):
        async with AsyncSessionLocal() as session:
            try:
                task = await create_or_resume_task(
                    session,
                    task_id=task_id,
                    context_id=context_id,
                    tenant_id=tenant_id,
                    owner_user_id=owner_user_id,
                    owner_role=str(user.get("role") or "USER").upper(),
                    message=normalized_message,
                    push_notifications=push_notifications,
                    callback_url=callback_url,
                    callback_auth_ref=(
                        str(push_config.get("authentication") or "") or None
                    ),
                )
                await session.commit()
                if push_notifications:
                    return _result(request_id, await task_to_a2a(session, task))
            except LookupError:
                return _error(request_id, -32001, "Task not found")

    result = await execute_task(
        AsyncSessionLocal,
        task_id=task_id,
        tenant_id=tenant_id,
    )
    return _result(request_id, result)


async def _get_task(request_id: Any, params: dict[str, Any], user: dict) -> JSONResponse:
    task_id = str(params.get("id") or params.get("taskId") or "")
    with tenant_scope(_tenant(user)):
        async with AsyncSessionLocal() as session:
            task = await get_owned_task(
                session,
                task_id=task_id,
                tenant_id=_tenant(user),
                owner_user_id=_owner(user),
            )
            if task is None:
                return _error(request_id, -32001, "Task not found")
            return _result(request_id, await task_to_a2a(session, task))


async def _cancel_task(request_id: Any, params: dict[str, Any], user: dict) -> JSONResponse:
    task_id = str(params.get("id") or params.get("taskId") or "")
    with tenant_scope(_tenant(user)):
        async with AsyncSessionLocal() as session:
            try:
                task = await cancel_owned_task(
                    session,
                    task_id=task_id,
                    tenant_id=_tenant(user),
                    owner_user_id=_owner(user),
                )
            except LookupError:
                return _error(request_id, -32001, "Task not found")
            except ValueError as exc:
                return _error(request_id, -32002, str(exc))
            return _result(request_id, await task_to_a2a(session, task))


async def _set_push_config(request_id: Any, params: dict[str, Any], user: dict) -> JSONResponse:
    task_id = str(params.get("id") or params.get("taskId") or "")
    config = params.get("pushNotificationConfig") or {}
    if not isinstance(config, Mapping):
        return _error(request_id, -32602, "pushNotificationConfig must be an object")
    try:
        callback_url = validate_callback_url(str(config.get("url") or "") or None)
    except ValueError as exc:
        return _error(request_id, -32602, str(exc))
    if not callback_url:
        return _error(request_id, -32602, "push notification callback URL is required")
    with tenant_scope(_tenant(user)):
        async with AsyncSessionLocal() as session:
            task = await get_owned_task(
                session,
                task_id=task_id,
                tenant_id=_tenant(user),
                owner_user_id=_owner(user),
            )
            if task is None:
                return _error(request_id, -32001, "Task not found")
            task.push_notifications = True
            task.callback_url = callback_url
            task.callback_auth_ref = str(config.get("authentication") or "") or None
            if task.status in TERMINAL_STATES:
                task.callback_status = "PENDING"
            await session.commit()
            return _result(
                request_id,
                {
                    "taskId": task.task_id,
                    "pushNotificationConfig": {
                        "url": task.callback_url,
                        "authentication": task.callback_auth_ref,
                    },
                },
            )


async def _get_push_config(request_id: Any, params: dict[str, Any], user: dict) -> JSONResponse:
    task_id = str(params.get("id") or params.get("taskId") or "")
    with tenant_scope(_tenant(user)):
        async with AsyncSessionLocal() as session:
            task = await get_owned_task(
                session,
                task_id=task_id,
                tenant_id=_tenant(user),
                owner_user_id=_owner(user),
            )
            if task is None:
                return _error(request_id, -32001, "Task not found")
            return _result(
                request_id,
                {
                    "taskId": task.task_id,
                    "pushNotificationConfig": {
                        "url": task.callback_url,
                        "authentication": task.callback_auth_ref,
                    },
                },
            )


def _tenant(user: dict) -> str:
    return str(user.get("tenant_id") or get_settings().default_tenant_id)


def _owner(user: dict) -> str:
    return str(user.get("user_id") or "unknown")


def _result(request_id: Any, result: Any) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": request_id, "result": result})


def _error(request_id: Any, code: int, message: str) -> JSONResponse:
    return JSONResponse(
        {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}
    )
