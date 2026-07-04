"""Authenticated APIs for governed ERP connector execution."""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Any
import hmac
import uuid
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from app.agent.tool_gateway import (
    TOOL_SPECS,
    ToolExecutionContext,
    execute_erp_connector_tool_async,
)
from app.core.auth import get_optional_user
from app.core.config import get_settings
from app.db.database import AsyncSessionLocal
from app.db.models import ErpConnectorStatus, ExternalSystemConnector
from app.erp.refund_saga import RefundFinanceCommand, execute_refund_finance_saga
from app.erp.runtime import (
    ERPConnectorError,
    connector_runtime_health,
    load_connector_runtime_config,
)


router = APIRouter()


class ConnectorToolExecutionPayload(BaseModel):
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    approval_id: str | None = None
    dry_run: bool = False
    scenario: str = "erp_integration"


class RefundFinancePayload(BaseModel):
    order_id: str
    refund_request_id: str
    open_item_id: str
    amount: Decimal = Field(gt=0, decimal_places=2, max_digits=18)
    currency: str = Field(default="CNY", min_length=3, max_length=3)
    connector_id: str = "CONN-MOCK-ERP"
    reason_code: str = "CUSTOMER_RETURN"
    approval_id: str | None = None
    dry_run: bool = False


class ConnectorConfigurationPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=2, max_length=160)
    system_type: str = Field(default="sap_s4hana", min_length=2, max_length=80)
    base_url: str = Field(min_length=1, max_length=300)
    auth_type: str = Field(default="oauth2_client_credentials", max_length=60)
    status: str = "DRAFT"
    mode: str = "mock"
    read_only: bool = True
    shadow_writes: bool = True
    verify_tls: bool = True
    timeout_seconds: float = Field(default=15.0, ge=1.0, le=120.0)
    max_retries: int = Field(default=2, ge=0, le=5)
    circuit_failure_threshold: int = Field(default=3, ge=1, le=20)
    circuit_reset_seconds: int = Field(default=30, ge=5, le=900)
    operation_paths: dict[str, str] = Field(default_factory=dict)
    capabilities: dict[str, Any] = Field(default_factory=dict)
    change_ticket: str | None = Field(default=None, max_length=100)


async def _runtime_user(
    user: Annotated[dict | None, Depends(get_optional_user)],
    admin_api_key: Annotated[str | None, Header(alias="X-Admin-API-Key")] = None,
) -> dict:
    configured_admin_key = get_settings().admin_api_key
    if configured_admin_key and admin_api_key and hmac.compare_digest(configured_admin_key, admin_api_key):
        return {"user_id": "admin-service", "role": "ADMIN"}
    if user:
        return user
    if get_settings().environment == "development":
        return {"user_id": "local-admin", "role": "MANAGER"}
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication is required for ERP connector operations.",
    )


def _context(
    user: dict,
    *,
    approval_id: str | None,
    dry_run: bool,
    scenario: str,
    trace_id: str | None,
    principal_token: str | None,
) -> ToolExecutionContext:
    return ToolExecutionContext(
        actor_role=str(user.get("role") or "USER").upper(),
        requested_by_role=str(user.get("role") or "USER").upper(),
        user_id=str(user.get("user_id") or "unknown"),
        trace_id=trace_id or str(uuid.uuid4()),
        scenario=scenario,
        dry_run=dry_run,
        tenant_id=str(user.get("tenant_id") or get_settings().default_tenant_id),
        approval_id=approval_id,
        principal_token=principal_token,
    )


def _require_connector_admin(user: dict) -> None:
    role = str(user.get("role") or "USER").upper()
    if role not in {"MANAGER", "FINANCE", "SECURITY", "ADMIN"}:
        raise HTTPException(status_code=403, detail="Connector configuration requires an administrative role.")


def _validate_connector_payload(payload: ConnectorConfigurationPayload) -> None:
    mode = payload.mode.lower()
    if mode not in {"mock", "live"}:
        raise HTTPException(status_code=400, detail="Connector mode must be 'mock' or 'live'.")
    if payload.auth_type not in {
        "none",
        "basic",
        "api_key",
        "bearer",
        "oauth2_client_credentials",
        "principal_propagation",
    }:
        raise HTTPException(status_code=400, detail="Unsupported connector auth_type.")
    parsed = urlparse(payload.base_url)
    if mode == "live" and parsed.scheme != "https":
        raise HTTPException(status_code=400, detail="Live connector base_url must use HTTPS.")
    for operation, path in payload.operation_paths.items():
        if not operation or not path.startswith("/") or "://" in path:
            raise HTTPException(
                status_code=400,
                detail=f"Operation path '{operation}' must be an absolute path, not a URL.",
            )
    if mode == "live" and not payload.read_only and not payload.shadow_writes and not payload.change_ticket:
        raise HTTPException(
            status_code=400,
            detail="Disabling both read-only and shadow mode requires a change_ticket.",
        )


@router.get("/runtime/{connector_id}/config")
async def get_runtime_config(
    connector_id: str,
    _: Annotated[dict, Depends(_runtime_user)],
) -> dict[str, Any]:
    config = await load_connector_runtime_config(connector_id)
    return {"connector": config.public_summary()}


@router.get("/runtime/{connector_id}/health")
async def get_runtime_health(
    connector_id: str,
    _: Annotated[dict, Depends(_runtime_user)],
) -> dict[str, Any]:
    try:
        return await connector_runtime_health(connector_id)
    except ERPConnectorError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.put("/runtime/{connector_id}/config")
async def update_runtime_config(
    connector_id: str,
    payload: ConnectorConfigurationPayload,
    user: Annotated[dict, Depends(_runtime_user)],
) -> dict[str, Any]:
    """Persist non-secret connector metadata; credentials remain environment-only."""

    _require_connector_admin(user)
    _validate_connector_payload(payload)
    try:
        connector_status = ErpConnectorStatus(payload.status.upper())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Unsupported connector status.") from exc

    async with AsyncSessionLocal() as session:
        record = await session.get(ExternalSystemConnector, connector_id)
        if record is None:
            record = ExternalSystemConnector(
                connector_id=connector_id,
                name=payload.name,
                system_type=payload.system_type,
                base_url=payload.base_url.rstrip("/"),
                auth_type=payload.auth_type,
                status=connector_status,
            )
            session.add(record)
        record.name = payload.name
        record.system_type = payload.system_type
        record.base_url = payload.base_url.rstrip("/")
        record.auth_type = payload.auth_type
        record.status = connector_status
        record.capabilities = payload.capabilities
        record.config = {
            "mode": payload.mode.lower(),
            "auth_type": payload.auth_type,
            "read_only": payload.read_only,
            "shadow_writes": payload.shadow_writes,
            "verify_tls": payload.verify_tls,
            "timeout_seconds": payload.timeout_seconds,
            "max_retries": payload.max_retries,
            "circuit_failure_threshold": payload.circuit_failure_threshold,
            "circuit_reset_seconds": payload.circuit_reset_seconds,
            "operation_paths": payload.operation_paths,
            "change_ticket": payload.change_ticket,
            "updated_by": str(user.get("user_id") or "unknown"),
        }
        await session.commit()

    config = await load_connector_runtime_config(connector_id)
    return {
        "connector": config.public_summary(),
        "secret_policy": {
            "persisted": False,
            "required_environment_variables": _required_secret_variables(payload.auth_type),
        },
    }


@router.post("/runtime/tools/execute")
async def execute_connector_tool(
    payload: ConnectorToolExecutionPayload,
    user: Annotated[dict, Depends(_runtime_user)],
    trace_id: Annotated[str | None, Header(alias="X-Trace-ID")] = None,
    principal_token: Annotated[str | None, Header(alias="X-SAP-Principal-Token")] = None,
) -> dict[str, Any]:
    if payload.tool_name not in TOOL_SPECS or not payload.tool_name.startswith("erp_"):
        raise HTTPException(status_code=400, detail="Only registered ERP tools can use this endpoint.")
    result = await execute_erp_connector_tool_async(
        payload.tool_name,
        payload.arguments,
        context=_context(
            user,
            approval_id=payload.approval_id,
            dry_run=payload.dry_run,
            scenario=payload.scenario,
            trace_id=trace_id,
            principal_token=principal_token,
        ),
    )
    status_code = 200 if result.success else (403 if not result.authorized else 502)
    response = {
        "tool": result.tool_name,
        "success": result.success,
        "data": result.data,
        "error": result.error,
        "authorized": result.authorized,
        "dry_run": result.dry_run,
        "duration_ms": result.duration_ms,
        "idempotency_key": result.idempotency_key,
        "audit_event": result.audit_event,
    }
    if not result.success:
        raise HTTPException(status_code=status_code, detail=response)
    return response


@router.post("/runtime/refunds/execute-finance-saga")
async def execute_refund_finance(
    payload: RefundFinancePayload,
    user: Annotated[dict, Depends(_runtime_user)],
    trace_id: Annotated[str | None, Header(alias="X-Trace-ID")] = None,
    principal_token: Annotated[str | None, Header(alias="X-SAP-Principal-Token")] = None,
) -> dict[str, Any]:
    result = await execute_refund_finance_saga(
        RefundFinanceCommand(
            order_id=payload.order_id,
            refund_request_id=payload.refund_request_id,
            open_item_id=payload.open_item_id,
            amount=payload.amount,
            currency=payload.currency.upper(),
            connector_id=payload.connector_id,
            reason_code=payload.reason_code,
        ),
        context=_context(
            user,
            approval_id=payload.approval_id,
            dry_run=payload.dry_run,
            scenario="refund_finance_posting",
            trace_id=trace_id,
            principal_token=principal_token,
        ),
    )
    if not result["success"]:
        raise HTTPException(status_code=502, detail=result)
    return result


def _required_secret_variables(auth_type: str) -> list[str]:
    return {
        "none": [],
        "basic": ["SAP_USERNAME", "SAP_PASSWORD"],
        "api_key": ["SAP_API_KEY"],
        "bearer": ["SAP_BEARER_TOKEN"],
        "oauth2_client_credentials": ["SAP_TOKEN_URL", "SAP_CLIENT_ID", "SAP_CLIENT_SECRET"],
        "principal_propagation": ["X-SAP-Principal-Token from trusted identity gateway"],
    }.get(auth_type, [])
