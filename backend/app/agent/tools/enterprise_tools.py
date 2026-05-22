"""Deterministic enterprise tools for non-refund scenarios."""

from __future__ import annotations

from datetime import datetime
import hashlib

from langchain_core.tools import tool
from pydantic import BaseModel, Field


def _stable_id(prefix: str, *parts: object) -> str:
    raw = ":".join(str(part or "") for part in parts)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12].upper()
    return f"{prefix}_{digest}"


class CreatePermissionRequestInput(BaseModel):
    system: str = Field(description="Target system, for example GitHub, CRM, finance, or production database.")
    permission_level: str = Field(description="Requested permission level, for example read, write, admin, or approve.")
    reason: str = Field(description="Business reason for requesting the permission.")
    requester_id: str = Field(description="User id of the requester.")


class CreateReimbursementRequestInput(BaseModel):
    amount: float = Field(description="Expense amount in CNY.", ge=0)
    category: str = Field(description="Expense category, for example travel, meal, hotel, taxi, or office.")
    description: str = Field(description="Business description of the expense.")
    requester_id: str = Field(description="User id of the requester.")


@tool(args_schema=CreatePermissionRequestInput)
def create_permission_request(
    system: str,
    permission_level: str,
    reason: str,
    requester_id: str,
) -> dict:
    """Create a permission request record in the simulated enterprise workflow."""

    request_id = _stable_id("ACCESS_REQ", requester_id, system, permission_level, reason)
    return {
        "success": True,
        "requestId": request_id,
        "system": system,
        "permissionLevel": permission_level,
        "reason": reason,
        "requesterId": requester_id,
        "status": "pending_review",
        "createdAt": datetime.utcnow().isoformat(),
    }


@tool(args_schema=CreateReimbursementRequestInput)
def create_reimbursement_request(
    amount: float,
    category: str,
    description: str,
    requester_id: str,
) -> dict:
    """Create a reimbursement request record in the simulated enterprise workflow."""

    request_id = _stable_id("EXPENSE_REQ", requester_id, f"{amount:.2f}", category, description)
    return {
        "success": True,
        "requestId": request_id,
        "amount": round(float(amount or 0), 2),
        "category": category,
        "description": description,
        "requesterId": requester_id,
        "status": "pending_review",
        "createdAt": datetime.utcnow().isoformat(),
    }
