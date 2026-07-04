"""Canonical-to-SAP field maps and SAP error normalization."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


CANONICAL_TO_SAP: dict[str, dict[str, str]] = {
    "sales_order": {
        "orderId": "SalesOrder",
        "customerId": "SoldToParty",
        "currency": "TransactionCurrency",
        "amount": "TotalNetAmount",
        "status": "OverallSDProcessStatus",
    },
    "credit_memo_request": {
        "order_id": "ReferenceSDDocument",
        "refund_request_id": "PurchaseOrderByCustomer",
        "currency": "TransactionCurrency",
        "amount": "RequestedCreditMemoAmount",
        "reason_code": "SDDocumentReason",
        "creditMemoId": "CreditMemoRequest",
    },
    "business_partner": {
        "partnerId": "BusinessPartner",
        "name": "BusinessPartnerFullName",
        "category": "BusinessPartnerCategory",
    },
    "delivery_document": {
        "deliveryId": "DeliveryDocument",
        "orderId": "ReferenceSDDocument",
        "status": "OverallGoodsMovementStatus",
        "shippingPoint": "ShippingPoint",
    },
    "billing_document": {
        "billingDocumentId": "BillingDocument",
        "orderId": "SalesDocument",
        "currency": "TransactionCurrency",
        "amount": "TotalNetAmount",
        "status": "OverallBillingStatus",
    },
    "open_item": {
        "companyCode": "CompanyCode",
        "fiscalYear": "FiscalYear",
        "accountingDocument": "AccountingDocument",
        "customerId": "Customer",
        "currency": "TransactionCurrency",
        "amount": "AmountInTransactionCurrency",
        "clearingDocument": "ClearingAccountingDocument",
    },
}


def to_sap(entity: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    mapping = CANONICAL_TO_SAP.get(entity, {})
    return {mapping.get(key, key): value for key, value in payload.items() if value is not None}


def from_sap(entity: str, payload: Mapping[str, Any]) -> dict[str, Any]:
    reverse = {value: key for key, value in CANONICAL_TO_SAP.get(entity, {}).items()}
    return {reverse.get(key, key): value for key, value in payload.items()}


def normalize_sap_response(entity: str | None, payload: Any) -> Any:
    if not entity:
        return payload
    if isinstance(payload, list):
        return [from_sap(entity, item) if isinstance(item, Mapping) else item for item in payload]
    if isinstance(payload, Mapping):
        return from_sap(entity, payload)
    return payload


@dataclass(frozen=True)
class SAPErrorDetail:
    code: str = ""
    message: str = ""
    target: str = ""
    severity: str = "error"
    details: list[dict[str, Any]] = field(default_factory=list)
    transaction_id: str = ""


def parse_sap_error(payload: Any, headers: Mapping[str, str] | None = None) -> SAPErrorDetail:
    error = payload.get("error", {}) if isinstance(payload, Mapping) else {}
    if not isinstance(error, Mapping):
        error = {}
    message = error.get("message", "")
    if isinstance(message, Mapping):
        message = message.get("value") or message.get("message") or ""
    inner = error.get("innererror") or error.get("innerError") or {}
    if not isinstance(inner, Mapping):
        inner = {}
    details = error.get("details") or inner.get("errordetails") or inner.get("errorDetails") or []
    if not isinstance(details, list):
        details = []
    response_headers = headers or {}
    return SAPErrorDetail(
        code=str(error.get("code") or ""),
        message=str(message or ""),
        target=str(error.get("target") or ""),
        severity=str(error.get("severity") or "error"),
        details=[dict(item) for item in details if isinstance(item, Mapping)],
        transaction_id=str(
            inner.get("transactionid")
            or inner.get("transactionId")
            or response_headers.get("sap-message-id")
            or response_headers.get("x-request-id")
            or ""
        ),
    )
