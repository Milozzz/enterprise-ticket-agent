"""Governed execution runtime for Mock ERP and SAP OData connectors."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import json
import time
from typing import Any, Mapping
from urllib.parse import quote

import httpx
from sqlalchemy import select

from app.core.config import get_settings
from app.db.database import AsyncSessionLocal
from app.core.masking import mask_dict
from app.db.models import AuditLog, ErpIdempotencyStatus, ExternalSystemConnector, IdempotencyRecord
from app.db.tenant_context import current_tenant_id, tenant_scope
from app.erp.sap_mapping import normalize_sap_response, parse_sap_error, to_sap


RETRYABLE_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504}
WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


class ERPConnectorError(RuntimeError):
    """Base error returned by the connector execution boundary."""


class ERPConnectorConfigurationError(ERPConnectorError):
    pass


class ERPConnectorUnavailableError(ERPConnectorError):
    pass


class ERPWriteBlockedError(ERPConnectorError):
    pass


@dataclass(frozen=True)
class AgentTraceRef:
    """F2：把 agent 侧链路标识带进 ERP 连接器审计，实现端到端 trace join。"""

    trace_id: str | None = None
    thread_id: str | None = None
    scenario: str | None = None
    actor_role: str | None = None


@dataclass(frozen=True)
class ConnectorRuntimeConfig:
    connector_id: str
    mode: str = "mock"
    base_url: str = ""
    auth_type: str = "none"
    api_key: str = field(default="", repr=False)
    username: str = field(default="", repr=False)
    password: str = field(default="", repr=False)
    bearer_token: str = field(default="", repr=False)
    client_id: str = field(default="", repr=False)
    client_secret: str = field(default="", repr=False)
    token_url: str = ""
    scope: str = ""
    verify_tls: bool = True
    read_only: bool = True
    shadow_writes: bool = True
    timeout_seconds: float = 15.0
    max_retries: int = 2
    circuit_failure_threshold: int = 3
    circuit_reset_seconds: int = 30
    operation_paths: dict[str, str] = field(default_factory=dict)

    def public_summary(self) -> dict[str, Any]:
        return {
            "connector_id": self.connector_id,
            "mode": self.mode,
            "base_url_configured": bool(self.base_url),
            "auth_type": self.auth_type,
            "verify_tls": self.verify_tls,
            "read_only": self.read_only,
            "shadow_writes": self.shadow_writes,
            "timeout_seconds": self.timeout_seconds,
            "max_retries": self.max_retries,
            "operation_paths": sorted(self.operation_paths),
        }


@dataclass(frozen=True)
class ConnectorExecutionResult:
    connector_id: str
    operation: str
    success: bool
    mode: str
    data: Any = None
    status_code: int | None = None
    duration_ms: int = 0
    attempts: int = 1
    shadow: bool = False
    replayed: bool = False
    request_id: str = ""
    remote_request_id: str | None = None
    etag: str | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "connectorId": self.connector_id,
            "operation": self.operation,
            "success": self.success,
            "mode": self.mode,
            "data": self.data,
            "statusCode": self.status_code,
            "durationMs": self.duration_ms,
            "attempts": self.attempts,
            "shadow": self.shadow,
            "replayed": self.replayed,
            "requestId": self.request_id,
            "remoteRequestId": self.remote_request_id,
            "etag": self.etag,
            "error": self.error,
        }


@dataclass
class _CircuitState:
    failures: int = 0
    opened_at: float | None = None


_CIRCUITS: dict[str, _CircuitState] = {}
_TOKEN_CACHE: dict[str, tuple[str, float]] = {}
# Per-connector 长连接客户端，复用 TCP/TLS 连接池，避免每请求重建握手。
_HTTP_CLIENTS: dict[str, httpx.AsyncClient] = {}


def _http_client(config: "ConnectorRuntimeConfig") -> httpx.AsyncClient:
    # Keep clients isolated per logical connector. Different tenants can point
    # at the same SAP host with different lifecycle and authentication config;
    # sharing only by URL also leaks stale test/config clients across runtimes.
    key = (
        f"{config.connector_id}|{config.base_url}|"
        f"{config.verify_tls}|{config.timeout_seconds}"
    )
    client = _HTTP_CLIENTS.get(key)
    if client is None or client.is_closed:
        client = httpx.AsyncClient(
            timeout=config.timeout_seconds,
            verify=config.verify_tls,
            follow_redirects=False,
        )
        _HTTP_CLIENTS[key] = client
    return client


async def close_erp_http_clients() -> None:
    """Close pooled connector clients on shutdown."""
    for client in list(_HTTP_CLIENTS.values()):
        try:
            await client.aclose()
        except Exception:
            pass
    _HTTP_CLIENTS.clear()


def _parse_operation_paths(raw: str | Mapping[str, Any] | None) -> dict[str, str]:
    if not raw:
        return {}
    value: Mapping[str, Any]
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ERPConnectorConfigurationError("SAP_OPERATION_PATHS_JSON must be valid JSON.") from exc
        if not isinstance(parsed, Mapping):
            raise ERPConnectorConfigurationError("SAP operation paths must be a JSON object.")
        value = parsed
    else:
        value = raw
    return {str(key): str(path) for key, path in value.items() if path}


async def load_connector_runtime_config(connector_id: str) -> ConnectorRuntimeConfig:
    """Load public connector metadata from DB and secrets from environment."""

    settings = get_settings()
    normalized_id = str(connector_id or "CONN-MOCK-ERP")
    if normalized_id == "CONN-MOCK-ERP":
        return ConnectorRuntimeConfig(
            connector_id=normalized_id,
            mode="mock",
            auth_type="none",
            read_only=False,
            shadow_writes=False,
        )

    record: ExternalSystemConnector | None = None
    try:
        async with AsyncSessionLocal() as session:
            record = await session.scalar(
                select(ExternalSystemConnector).where(ExternalSystemConnector.connector_id == normalized_id)
            )
    except Exception:
        # A connector can still be configured entirely through environment
        # variables when the metadata database is temporarily unavailable.
        record = None

    record_config = dict(record.config or {}) if record else {}
    db_paths = record_config.get("operation_paths") or {}
    env_paths = _parse_operation_paths(settings.sap_operation_paths_json)
    operation_paths = {**_parse_operation_paths(db_paths), **env_paths}
    mode = str(record_config.get("mode") or settings.sap_connector_mode or "mock").lower()
    base_url = str(settings.sap_base_url or (record.base_url if record else "")).rstrip("/")
    auth_type = str(
        record_config.get("auth_type")
        or (record.auth_type if record else "")
        or settings.sap_auth_type
        or "none"
    )

    return ConnectorRuntimeConfig(
        connector_id=normalized_id,
        mode=mode,
        base_url=base_url,
        auth_type=auth_type.lower(),
        api_key=settings.sap_api_key,
        username=settings.sap_username,
        password=settings.sap_password,
        bearer_token=settings.sap_bearer_token,
        client_id=settings.sap_client_id,
        client_secret=settings.sap_client_secret,
        token_url=settings.sap_token_url,
        scope=settings.sap_scope,
        verify_tls=_as_bool(record_config.get("verify_tls", settings.sap_verify_tls)),
        read_only=_as_bool(record_config.get("read_only", settings.sap_read_only)),
        shadow_writes=_as_bool(record_config.get("shadow_writes", settings.sap_shadow_writes)),
        timeout_seconds=float(record_config.get("timeout_seconds", settings.sap_timeout_seconds)),
        max_retries=int(record_config.get("max_retries", settings.sap_max_retries)),
        circuit_failure_threshold=int(
            record_config.get("circuit_failure_threshold", settings.sap_circuit_failure_threshold)
        ),
        circuit_reset_seconds=int(record_config.get("circuit_reset_seconds", settings.sap_circuit_reset_seconds)),
        operation_paths=operation_paths,
    )


def connector_request_id(envelope: Mapping[str, Any]) -> str:
    raw = json.dumps(dict(envelope), sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _as_bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


class MockERPConnector:
    def __init__(self, config: ConnectorRuntimeConfig):
        self.config = config

    async def health(self) -> dict[str, Any]:
        return {"status": "healthy", "mode": "mock", "latency_ms": 0}

    async def execute(
        self,
        envelope: Mapping[str, Any],
        *,
        principal_token: str | None = None,
        force_write: bool = False,
    ) -> ConnectorExecutionResult:
        del principal_token, force_write
        started = time.perf_counter()
        operation = str(envelope.get("operation") or "unknown")
        payload = dict(envelope.get("payload") or {})
        request_id = connector_request_id(envelope)
        digest = hashlib.sha256(request_id.encode("utf-8")).hexdigest()[:12].upper()
        if operation == "get_order":
            data = {
                "orderId": payload.get("order_id"),
                "sourceSystem": "MINI_ERP",
                "status": "available",
            }
        elif operation == "query_doctype":
            data = {
                "doctype": payload.get("doctype"),
                "query": payload.get("query", {}),
                "value": [],
            }
        elif operation == "create_credit_memo":
            data = {
                "creditMemoId": f"CM-{digest}",
                "status": "POSTED",
                **payload,
            }
        elif operation == "clear_open_item":
            data = {
                "clearingDocumentId": f"CLR-{digest}",
                "status": "POSTED",
                **payload,
            }
        elif operation == "reverse_document":
            data = {
                "reversalDocumentId": f"REV-{digest}",
                "status": "POSTED",
                **payload,
            }
        elif operation == "batch":
            data = {
                "responses": [
                    {"id": item.get("id", str(index)), "status": 200, "body": {"mock": True}}
                    for index, item in enumerate(payload.get("requests") or [], start=1)
                ]
            }
        else:
            raise ERPConnectorConfigurationError(f"Mock connector does not implement operation '{operation}'.")
        return ConnectorExecutionResult(
            connector_id=self.config.connector_id,
            operation=operation,
            success=True,
            mode="mock",
            data=data,
            status_code=200,
            duration_ms=max(0, int((time.perf_counter() - started) * 1000)),
            request_id=request_id,
        )


class SAPODataConnector:
    DEFAULT_OPERATION_PATHS = {
        "get_order": "/sap/opu/odata/sap/API_SALES_ORDER_SRV/A_SalesOrder('{order_id}')",
        "create_credit_memo": "/sap/opu/odata/sap/API_CREDIT_MEMO_REQUEST_SRV/A_CreditMemoRequest",
        "batch": "/sap/opu/odata/sap/API_CREDIT_MEMO_REQUEST_SRV/$batch",
    }
    DEFAULT_DOCTYPE_PATHS = {
        "business_partner": "/sap/opu/odata/sap/API_BUSINESS_PARTNER/A_BusinessPartner",
        "delivery_document": "/sap/opu/odata/sap/API_OUTBOUND_DELIVERY_SRV_0002/A_OutbDeliveryHeader",
        "billing_document": "/sap/opu/odata/sap/API_BILLING_DOCUMENT_SRV/A_BillingDocument",
        "open_item": "/sap/opu/odata/sap/API_OPLACCTGDOCITEMCUBE_SRV/A_OperationalAcctgDocItemCube",
    }

    def __init__(self, config: ConnectorRuntimeConfig):
        self.config = config
        if config.mode == "live" and not config.base_url:
            raise ERPConnectorConfigurationError("SAP_BASE_URL is required for live connector mode.")

    async def health(self) -> dict[str, Any]:
        if self.config.mode != "live":
            return {"status": "configured", "mode": self.config.mode, **self.config.public_summary()}
        started = time.perf_counter()
        request = {
            "connectorId": self.config.connector_id,
            "operation": "query_doctype",
            "method": "GET",
            "payload": {"doctype": "business_partner", "query": {"$top": 1}},
        }
        try:
            result = await self.execute(request)
            return {
                "status": "healthy" if result.success else "unhealthy",
                "latency_ms": max(0, int((time.perf_counter() - started) * 1000)),
                "status_code": result.status_code,
                **self.config.public_summary(),
            }
        except ERPConnectorError as exc:
            return {
                "status": "unhealthy",
                "latency_ms": max(0, int((time.perf_counter() - started) * 1000)),
                "error": str(exc),
                **self.config.public_summary(),
            }

    async def execute(
        self,
        envelope: Mapping[str, Any],
        *,
        principal_token: str | None = None,
        force_write: bool = False,
    ) -> ConnectorExecutionResult:
        operation = str(envelope.get("operation") or "")
        method = str(envelope.get("method") or "GET").upper()
        payload = dict(envelope.get("payload") or {})
        request_id = connector_request_id(envelope)
        is_write = method in WRITE_METHODS
        if is_write and self.config.shadow_writes and not force_write:
            return ConnectorExecutionResult(
                connector_id=self.config.connector_id,
                operation=operation,
                success=True,
                mode=self.config.mode,
                data={"plannedRequest": self._safe_request_preview(envelope)},
                status_code=202,
                shadow=True,
                request_id=request_id,
            )
        if is_write and self.config.read_only and not force_write:
            raise ERPWriteBlockedError("SAP connector is read-only. Set SAP_READ_ONLY=false after validation.")

        self._assert_circuit_available()
        path = self._operation_path(operation, payload)
        url = f"{self.config.base_url}{path}"
        query_params = payload.get("query") if method == "GET" else None
        body = self._business_payload(operation, payload) if is_write else None
        headers = {
            "Accept": "application/json",
            "X-Correlation-ID": request_id,
        }
        idempotency_key = envelope.get("idempotencyKey")
        if idempotency_key:
            headers["Idempotency-Key"] = str(idempotency_key)
        etag = payload.get("etag")
        if etag:
            headers["If-Match"] = str(etag)

        started = time.perf_counter()
        attempts = 0
        try:
            # 复用 per-connector 的连接池客户端，避免每个请求重建 TCP/TLS 连接。
            # cookies 按请求传递（而非 mutate 共享 client.cookies），避免跨请求串 CSRF 会话。
            client = _http_client(self.config)
            auth, auth_headers = await self._authentication(client, principal_token)
            headers.update(auth_headers)
            request_cookies = None
            if is_write:
                csrf_headers, request_cookies = await self._fetch_csrf_token(client, url, auth, headers)
                headers.update(csrf_headers)

            max_attempts = 1 + max(0, self.config.max_retries)
            can_retry = not is_write or bool(idempotency_key)
            response: httpx.Response | None = None
            while attempts < max_attempts:
                attempts += 1
                request_kwargs: dict[str, Any] = {
                    "method": method,
                    "url": url,
                    "params": query_params,
                    "headers": headers,
                    "auth": auth,
                }
                if request_cookies is not None:
                    request_kwargs["cookies"] = request_cookies
                if operation == "batch" and payload.get("batch_format") == "multipart":
                    content, content_type = self._multipart_batch(payload.get("requests") or [])
                    request_kwargs["content"] = content
                    request_kwargs["headers"] = {**headers, "Content-Type": content_type}
                else:
                    request_kwargs["json"] = body
                response = await client.request(**request_kwargs)
                if response.status_code not in RETRYABLE_STATUS_CODES or not can_retry or attempts >= max_attempts:
                    break
                await asyncio.sleep(self._retry_delay(response, attempts))

            assert response is not None
            duration_ms = max(0, int((time.perf_counter() - started) * 1000))
            if response.status_code >= 400:
                # 仅 5xx / 网络类失败视为连接器不可用并计入熔断；4xx 是业务/客户端错误
                # （校验失败、404、409 冲突、412 ETag 不匹配等），不应刷开熔断拖垮正常流量。
                if response.status_code >= 500:
                    self._record_failure()
                    raise ERPConnectorUnavailableError(self._safe_http_error(response))
                # 4xx：作为业务错误抛出，但不触发熔断，也不误判为“成功”。
                raise ERPConnectorError(self._safe_http_error(response))
            self._record_success()
            entity = {
                "get_order": "sales_order",
                "create_credit_memo": "credit_memo_request",
            }.get(operation)
            if operation == "query_doctype":
                entity = str(payload.get("doctype") or "")
            data = normalize_sap_response(entity, self._normalize_odata_response(response))
            return ConnectorExecutionResult(
                connector_id=self.config.connector_id,
                operation=operation,
                success=True,
                mode="live",
                data=data,
                status_code=response.status_code,
                duration_ms=duration_ms,
                attempts=attempts,
                request_id=request_id,
                remote_request_id=response.headers.get("x-correlationid")
                or response.headers.get("sap-message-id")
                or response.headers.get("x-request-id"),
                etag=response.headers.get("etag"),
            )
        except (httpx.HTTPError, asyncio.TimeoutError) as exc:
            self._record_failure()
            raise ERPConnectorUnavailableError(f"SAP connector request failed: {type(exc).__name__}") from exc

    def _operation_path(self, operation: str, payload: Mapping[str, Any]) -> str:
        if operation == "query_doctype":
            doctype = str(payload.get("doctype") or "")
            template = (
                self.config.operation_paths.get(f"doctype.{doctype}")
                or self.DEFAULT_DOCTYPE_PATHS.get(doctype)
            )
            if not template:
                raise ERPConnectorConfigurationError(
                    f"No SAP OData entity path configured for doctype '{doctype}'."
                )
        else:
            template = self.config.operation_paths.get(operation) or self.DEFAULT_OPERATION_PATHS.get(operation)
        if not template:
            raise ERPConnectorConfigurationError(f"No SAP operation path configured for '{operation}'.")
        format_values = {key: quote(str(value), safe="") for key, value in payload.items() if value is not None}
        try:
            path = template.format(**format_values)
        except KeyError as exc:
            raise ERPConnectorConfigurationError(
                f"SAP operation path for '{operation}' requires missing field {exc}."
            ) from exc
        return path if path.startswith("/") else f"/{path}"

    async def _authentication(
        self,
        client: httpx.AsyncClient,
        principal_token: str | None,
    ) -> tuple[httpx.Auth | None, dict[str, str]]:
        auth_type = self.config.auth_type
        if auth_type in {"none", ""}:
            return None, {}
        if auth_type == "basic":
            if not self.config.username or not self.config.password:
                raise ERPConnectorConfigurationError("SAP username/password are required for basic authentication.")
            return httpx.BasicAuth(self.config.username, self.config.password), {}
        if auth_type == "api_key":
            if not self.config.api_key:
                raise ERPConnectorConfigurationError("SAP_API_KEY is required for API key authentication.")
            return None, {"APIKey": self.config.api_key}
        if auth_type == "bearer":
            token = self.config.bearer_token
            if not token:
                raise ERPConnectorConfigurationError("SAP_BEARER_TOKEN is required for bearer authentication.")
            return None, {"Authorization": f"Bearer {token}"}
        if auth_type == "principal_propagation":
            if not principal_token:
                raise ERPConnectorConfigurationError(
                    "A named-user token is required for principal propagation. Technical-user fallback is forbidden."
                )
            return None, {"Authorization": f"Bearer {principal_token}"}
        if auth_type == "oauth2_client_credentials":
            token = await self._oauth_access_token(client)
            return None, {"Authorization": f"Bearer {token}"}
        raise ERPConnectorConfigurationError(f"Unsupported SAP auth type '{auth_type}'.")

    async def _oauth_access_token(self, client: httpx.AsyncClient) -> str:
        cache_key = f"{self.config.token_url}:{self.config.client_id}:{self.config.scope}"
        cached = _TOKEN_CACHE.get(cache_key)
        now = time.time()
        if cached and cached[1] > now + 30:
            return cached[0]
        if not self.config.token_url or not self.config.client_id or not self.config.client_secret:
            raise ERPConnectorConfigurationError(
                "SAP_TOKEN_URL, SAP_CLIENT_ID and SAP_CLIENT_SECRET are required for OAuth client credentials."
            )
        form = {"grant_type": "client_credentials"}
        if self.config.scope:
            form["scope"] = self.config.scope
        response = await client.post(
            self.config.token_url,
            data=form,
            auth=httpx.BasicAuth(self.config.client_id, self.config.client_secret),
            headers={"Accept": "application/json"},
        )
        if response.status_code >= 400:
            raise ERPConnectorUnavailableError(f"SAP OAuth token request failed with HTTP {response.status_code}.")
        payload = response.json()
        token = str(payload.get("access_token") or "")
        if not token:
            raise ERPConnectorUnavailableError("SAP OAuth response did not contain access_token.")
        expires_in = int(payload.get("expires_in") or 300)
        _TOKEN_CACHE[cache_key] = (token, now + expires_in)
        return token

    async def _fetch_csrf_token(
        self,
        client: httpx.AsyncClient,
        url: str,
        auth: httpx.Auth | None,
        headers: Mapping[str, str],
    ) -> tuple[dict[str, str], httpx.Cookies]:
        fetch_headers = {**dict(headers), "x-csrf-token": "Fetch"}
        response = await client.get(url, headers=fetch_headers, auth=auth)
        if response.status_code >= 400:
            raise ERPConnectorUnavailableError(
                f"SAP CSRF token fetch failed with HTTP {response.status_code}."
            )
        token = response.headers.get("x-csrf-token")
        if not token:
            raise ERPConnectorUnavailableError("SAP response did not provide an x-csrf-token header.")
        return {"x-csrf-token": token}, response.cookies

    @staticmethod
    def _business_payload(operation: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        ignored = {"query", "doctype", "etag"}
        business_payload = {key: value for key, value in payload.items() if key not in ignored}
        if operation == "create_credit_memo":
            return to_sap("credit_memo_request", business_payload)
        if operation == "batch":
            return {"requests": business_payload.get("requests") or []}
        return business_payload

    @staticmethod
    def _multipart_batch(requests: list[dict[str, Any]]) -> tuple[bytes, str]:
        boundary = f"batch_{hashlib.sha256(json.dumps(requests, sort_keys=True).encode()).hexdigest()[:16]}"
        parts: list[str] = []
        for index, request in enumerate(requests, start=1):
            method = str(request.get("method") or "GET").upper()
            url = str(request.get("url") or request.get("path") or "")
            body = request.get("body")
            lines = [
                f"--{boundary}",
                "Content-Type: application/http",
                "Content-Transfer-Encoding: binary",
                f"Content-ID: {index}",
                "",
                f"{method} {url} HTTP/1.1",
                "Accept: application/json",
            ]
            if body is not None:
                lines.extend(["Content-Type: application/json", "", json.dumps(body, separators=(",", ":"))])
            else:
                lines.append("")
            parts.append("\r\n".join(lines))
        parts.append(f"--{boundary}--")
        return "\r\n".join(parts).encode(), f"multipart/mixed; boundary={boundary}"

    @staticmethod
    def _normalize_odata_response(response: httpx.Response) -> Any:
        if not response.content:
            return {}
        payload = response.json()
        if isinstance(payload, Mapping) and "value" in payload:
            return payload["value"]
        if isinstance(payload, Mapping) and isinstance(payload.get("d"), Mapping):
            legacy = payload["d"]
            return legacy.get("results", legacy)
        return payload

    @staticmethod
    def _safe_http_error(response: httpx.Response) -> str:
        detail = ""
        code = ""
        transaction_id = ""
        try:
            payload = response.json()
            parsed = parse_sap_error(payload, response.headers)
            detail = parsed.message
            code = parsed.code
            transaction_id = parsed.transaction_id
        except Exception:
            detail = ""
        components = [item for item in [code, detail[:240], transaction_id] if item]
        suffix = f": {' | '.join(components)}" if components else ""
        return f"SAP OData request failed with HTTP {response.status_code}{suffix}"

    @staticmethod
    def _retry_delay(response: httpx.Response, attempt: int) -> float:
        retry_after = response.headers.get("retry-after")
        if retry_after and retry_after.isdigit():
            return min(float(retry_after), 5.0)
        return min(0.25 * (2 ** max(0, attempt - 1)), 2.0)

    def _assert_circuit_available(self) -> None:
        state = _CIRCUITS.setdefault(self.config.connector_id, _CircuitState())
        if state.opened_at is None:
            return
        if time.monotonic() - state.opened_at >= self.config.circuit_reset_seconds:
            state.failures = 0
            state.opened_at = None
            return
        raise ERPConnectorUnavailableError("SAP connector circuit is open after repeated failures.")

    def _record_failure(self) -> None:
        state = _CIRCUITS.setdefault(self.config.connector_id, _CircuitState())
        state.failures += 1
        if state.failures >= max(1, self.config.circuit_failure_threshold):
            state.opened_at = time.monotonic()

    def _record_success(self) -> None:
        state = _CIRCUITS.setdefault(self.config.connector_id, _CircuitState())
        state.failures = 0
        state.opened_at = None

    def _safe_request_preview(self, envelope: Mapping[str, Any]) -> dict[str, Any]:
        payload = dict(envelope.get("payload") or {})
        return {
            "method": str(envelope.get("method") or "GET").upper(),
            "operation": envelope.get("operation"),
            "path": self._operation_path(str(envelope.get("operation") or ""), payload),
            "payload": self._business_payload(str(envelope.get("operation") or ""), payload),
            "idempotencyKey": envelope.get("idempotencyKey"),
        }


async def get_connector(connector_id: str) -> MockERPConnector | SAPODataConnector:
    config = await load_connector_runtime_config(connector_id)
    if config.mode == "mock":
        return MockERPConnector(config)
    return SAPODataConnector(config)


async def execute_connector_envelope(
    envelope: Mapping[str, Any],
    *,
    principal_token: str | None = None,
    force_write: bool = False,
    tenant_id: str | None = None,
    agent_trace: "AgentTraceRef | None" = None,
) -> ConnectorExecutionResult:
    """F2（agent×数据结合层）：agent_trace 携带发起该 ERP 调用的 agent 侧
    thread_id/trace_id，写入连接器审计，使"用户说了什么 → 对 SAP 发了什么请求"
    可用同一 trace_id 端到端 join。为空时退回旧的 erp:{幂等键} 编号。"""
    resolved_tenant = (
        current_tenant_id() if not tenant_id or tenant_id == "default" else tenant_id
    )
    connector_id = str(envelope.get("connectorId") or "CONN-MOCK-ERP")
    connector = await get_connector(connector_id)
    method = str(envelope.get("method") or "GET").upper()
    idempotency_key = str(envelope.get("idempotencyKey") or "")
    request_hash = hashlib.sha256(
        json.dumps(dict(envelope), sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()

    replay = None
    if method in WRITE_METHODS and idempotency_key:
        replay = await _reserve_idempotency(
            connector_id=connector_id,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            fail_closed=connector.config.mode == "live",
            tenant_id=resolved_tenant,
        )
    if replay:
        result = _result_from_snapshot(replay, replayed=True)
        await _persist_connector_audit(envelope, result, tenant_id=resolved_tenant, agent_trace=agent_trace)
        return result

    try:
        result = await connector.execute(envelope, principal_token=principal_token, force_write=force_write)
    except Exception as exc:
        if method in WRITE_METHODS and idempotency_key:
            await _complete_idempotency(
                idempotency_key=idempotency_key,
                status=ErpIdempotencyStatus.FAILED,
                snapshot=None,
                fail_closed=False,
                tenant_id=resolved_tenant,
            )
        await _persist_connector_audit(
            envelope,
            ConnectorExecutionResult(
                connector_id=connector_id,
                operation=str(envelope.get("operation") or "unknown"),
                success=False,
                mode=connector.config.mode,
                request_id=connector_request_id(envelope),
                error=str(exc),
            ),
            tenant_id=resolved_tenant,
            agent_trace=agent_trace,
        )
        raise

    if method in WRITE_METHODS and idempotency_key:
        await _complete_idempotency(
            idempotency_key=idempotency_key,
            status=ErpIdempotencyStatus.COMPLETED,
            snapshot=result.to_dict(),
            fail_closed=connector.config.mode == "live",
            tenant_id=resolved_tenant,
        )
    await _persist_connector_audit(envelope, result, tenant_id=resolved_tenant, agent_trace=agent_trace)
    return result


async def connector_runtime_health(connector_id: str) -> dict[str, Any]:
    connector = await get_connector(connector_id)
    result = await connector.health()
    return {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "connector_id": connector_id,
        **result,
    }


async def _reserve_idempotency(
    *,
    connector_id: str,
    idempotency_key: str,
    request_hash: str,
    fail_closed: bool,
    tenant_id: str,
) -> dict[str, Any] | None:
    storage_key = _tenant_idempotency_key(tenant_id, idempotency_key)
    try:
        with tenant_scope(tenant_id):
            async with AsyncSessionLocal() as session:
                existing = await session.get(IdempotencyRecord, storage_key)
                if existing and existing.tenant_id != tenant_id:
                    raise ERPWriteBlockedError("Idempotency record belongs to another tenant.")
                if existing:
                    if existing.request_hash != request_hash:
                        raise ERPWriteBlockedError(
                            "Idempotency-Key was reused with a different request payload."
                        )
                    if existing.status == ErpIdempotencyStatus.COMPLETED and existing.response_snapshot:
                        return dict(existing.response_snapshot)
                    if existing.status == ErpIdempotencyStatus.IN_PROGRESS:
                        raise ERPWriteBlockedError("An ERP write with this Idempotency-Key is already in progress.")
                    existing.status = ErpIdempotencyStatus.IN_PROGRESS
                    existing.response_snapshot = None
                    existing.expires_at = _utcnow_naive() + timedelta(hours=24)
                else:
                    session.add(
                        IdempotencyRecord(
                            idempotency_key=storage_key,
                            tenant_id=tenant_id,
                            scope=f"erp_connector:{connector_id}",
                            request_hash=request_hash,
                            status=ErpIdempotencyStatus.IN_PROGRESS,
                            expires_at=_utcnow_naive() + timedelta(hours=24),
                        )
                    )
                await session.commit()
                return None
    except ERPConnectorError:
        raise
    except Exception as exc:
        if fail_closed:
            raise ERPWriteBlockedError(
                "Idempotency store is unavailable; live ERP write was blocked."
            ) from exc
        return None


async def _complete_idempotency(
    *,
    idempotency_key: str,
    status: ErpIdempotencyStatus,
    snapshot: dict[str, Any] | None,
    fail_closed: bool,
    tenant_id: str,
) -> None:
    storage_key = _tenant_idempotency_key(tenant_id, idempotency_key)
    try:
        with tenant_scope(tenant_id):
            async with AsyncSessionLocal() as session:
                record = await session.get(IdempotencyRecord, storage_key)
                if record and record.tenant_id == tenant_id:
                    record.status = status
                    record.response_snapshot = snapshot
                    await session.commit()
    except Exception as exc:
        if fail_closed:
            raise ERPWriteBlockedError(
                "ERP write completed but the idempotency result could not be persisted; reconciliation is required."
            ) from exc


def _result_from_snapshot(snapshot: Mapping[str, Any], *, replayed: bool) -> ConnectorExecutionResult:
    return ConnectorExecutionResult(
        connector_id=str(snapshot.get("connectorId") or ""),
        operation=str(snapshot.get("operation") or ""),
        success=bool(snapshot.get("success")),
        mode=str(snapshot.get("mode") or "unknown"),
        data=snapshot.get("data"),
        status_code=snapshot.get("statusCode"),
        duration_ms=int(snapshot.get("durationMs") or 0),
        attempts=int(snapshot.get("attempts") or 1),
        shadow=bool(snapshot.get("shadow")),
        replayed=replayed,
        request_id=str(snapshot.get("requestId") or ""),
        remote_request_id=snapshot.get("remoteRequestId"),
        etag=snapshot.get("etag"),
        error=snapshot.get("error"),
    )


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def _persist_connector_audit(
    envelope: Mapping[str, Any],
    result: ConnectorExecutionResult,
    *,
    tenant_id: str,
    agent_trace: "AgentTraceRef | None" = None,
) -> None:
    """Persist compact connector telemetry without secrets or full SAP payloads."""

    request_id = result.request_id or connector_request_id(envelope)
    idempotency_key = str(envelope.get("idempotencyKey") or "")
    input_data = mask_dict(
        {
            "connector_id": result.connector_id,
            "operation": result.operation,
            "method": str(envelope.get("method") or "GET").upper(),
            "payload": dict(envelope.get("payload") or {}),
            "idempotency_key": idempotency_key or None,
            # F2：把发起方 agent 场景/角色也记进审计输入，便于按场景检索 ERP 调用
            "agent_scenario": agent_trace.scenario if agent_trace else None,
            "agent_actor_role": agent_trace.actor_role if agent_trace else None,
        }
    )
    output_data = {
        "mode": result.mode,
        "status_code": result.status_code,
        "attempts": result.attempts,
        "shadow": result.shadow,
        "replayed": result.replayed,
        "remote_request_id": result.remote_request_id,
        "error": result.error,
        # 保留 ERP 侧原始编号，即便 join 到 agent trace 也能回溯连接器请求
        "connector_request_id": request_id,
    }
    # F2：优先用 agent 侧 thread/trace 编号，使 ERP 连接器审计与 agent 主链路
    # 可用同一 trace_id join；无 agent_trace（如后台对账直调）时退回 erp:{幂等键}。
    audit_thread_id = (
        (agent_trace.thread_id if agent_trace and agent_trace.thread_id else None)
        or f"erp:{idempotency_key or request_id}"
    )
    audit_trace_id = (
        (agent_trace.trace_id if agent_trace and agent_trace.trace_id else None)
        or request_id
    )
    try:
        with tenant_scope(tenant_id):
            async with AsyncSessionLocal() as session:
                session.add(
                    AuditLog(
                    tenant_id=tenant_id,
                    thread_id=audit_thread_id,
                    trace_id=audit_trace_id,
                    node_name="erp_connector",
                    event_type=result.operation,
                    input_data=input_data,
                    output_data=output_data,
                    duration_ms=result.duration_ms,
                    success=result.success,
                    )
                )
                await session.commit()
    except Exception:
        return


def _tenant_idempotency_key(tenant_id: str, idempotency_key: str) -> str:
    digest = hashlib.sha256(f"{tenant_id}:{idempotency_key}".encode()).hexdigest()[:32]
    return f"idem:{digest}"
