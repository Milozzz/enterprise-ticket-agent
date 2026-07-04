"""Connector abstraction for ERP-style systems.

This layer keeps Tool Gateway calls independent from a specific backend such as
Mock ERP, SAP OData, Odoo, or ERPNext. Builders return deterministic request
envelopes; ``app.erp.runtime`` executes those envelopes through a governed
connector runtime.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any


@dataclass(frozen=True)
class ERPConnectorRequest:
    connector_id: str
    operation: str
    method: str
    path: str
    payload: dict[str, Any]
    idempotency_key: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "connectorId": self.connector_id,
            "operation": self.operation,
            "method": self.method,
            "path": self.path,
            "payload": self.payload,
            "idempotencyKey": self.idempotency_key,
        }


def build_erp_get_order_request(order_id: str, connector_id: str = "CONN-MOCK-ERP") -> dict[str, Any]:
    return ERPConnectorRequest(
        connector_id=connector_id,
        operation="get_order",
        method="GET",
        path=f"/api/erp/orders/{order_id}",
        payload={"order_id": order_id, "expand": ["document_flow", "change_document"]},
    ).to_dict()


def build_erp_query_doctype_request(
    doctype: str,
    filter: str | None = None,
    select: str | None = None,
    orderby: str | None = None,
    top: int = 20,
    skip: int = 0,
    expand: str | None = None,
    connector_id: str = "CONN-MOCK-ERP",
) -> dict[str, Any]:
    query: dict[str, Any] = {"$top": top, "$skip": skip}
    if filter:
        query["$filter"] = filter
    if select:
        query["$select"] = select
    if orderby:
        query["$orderby"] = orderby
    if expand:
        query["$expand"] = expand
    return ERPConnectorRequest(
        connector_id=connector_id,
        operation="query_doctype",
        method="GET",
        path=f"/api/erp/doctype/{doctype}",
        payload={"doctype": doctype, "query": query},
    ).to_dict()


def build_erp_create_credit_memo_request(
    order_id: str,
    refund_request_id: str,
    amount: Decimal | float | str,
    currency: str = "CNY",
    connector_id: str = "CONN-MOCK-ERP",
) -> dict[str, Any]:
    money = _money(amount)
    idempotency_key = f"erp-credit-memo:{refund_request_id}:{money}:{currency}"
    return ERPConnectorRequest(
        connector_id=connector_id,
        operation="create_credit_memo",
        method="POST",
        path="/api/erp/doctype/credit_memo",
        payload={
            "order_id": order_id,
            "refund_request_id": refund_request_id,
            "amount": money,
            "currency": currency,
        },
        idempotency_key=idempotency_key,
    ).to_dict()


def build_erp_clear_open_item_request(
    credit_memo_id: str,
    open_item_id: str,
    amount: Decimal | float | str,
    currency: str = "CNY",
    connector_id: str = "CONN-MOCK-ERP",
) -> dict[str, Any]:
    money = _money(amount)
    idempotency_key = f"erp-clearing:{credit_memo_id}:{open_item_id}:{money}:{currency}"
    return ERPConnectorRequest(
        connector_id=connector_id,
        operation="clear_open_item",
        method="POST",
        path="/api/erp/finance/clear-open-item",
        payload={
            "credit_memo_id": credit_memo_id,
            "open_item_id": open_item_id,
            "amount": money,
            "currency": currency,
        },
        idempotency_key=idempotency_key,
    ).to_dict()


def build_erp_reverse_document_request(
    source_document_id: str,
    reason_code: str,
    amount: Decimal | float | str,
    currency: str = "CNY",
    connector_id: str = "CONN-MOCK-ERP",
) -> dict[str, Any]:
    money = _money(amount)
    idempotency_key = f"erp-reversal:{source_document_id}:{reason_code}"
    return ERPConnectorRequest(
        connector_id=connector_id,
        operation="reverse_document",
        method="POST",
        path="/api/erp/finance/reverse-document",
        payload={
            "source_document_id": source_document_id,
            "reason_code": reason_code,
            "amount": money,
            "currency": currency,
        },
        idempotency_key=idempotency_key,
    ).to_dict()


def build_erp_batch_request(
    requests: list[dict[str, Any]],
    *,
    batch_format: str = "json",
    connector_id: str = "CONN-MOCK-ERP",
) -> dict[str, Any]:
    if batch_format not in {"json", "multipart"}:
        raise ValueError("batch_format must be json or multipart")
    return ERPConnectorRequest(
        connector_id=connector_id,
        operation="batch",
        method="POST",
        path="/$batch",
        payload={"requests": requests, "batch_format": batch_format},
        idempotency_key=None,
    ).to_dict()


def _money(value: Decimal | float | str) -> str:
    amount = Decimal(str(value)).quantize(Decimal("0.01"))
    if amount < 0:
        raise ValueError("ERP amount cannot be negative")
    return format(amount, ".2f")
