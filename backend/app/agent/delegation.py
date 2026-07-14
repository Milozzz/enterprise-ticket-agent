"""Short-lived, scoped delegation tokens for Agent tool execution.

The database grant is the durable source of truth.  A signed token is the
portable proof carried through LangGraph state and re-checked at Tool Gateway.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import secrets
from typing import Any, Mapping, Sequence
import uuid

from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.models import AgentDelegationGrant


DELEGATION_ISSUER = "enterprise-ticket-agent"
DELEGATION_AUDIENCE = "tool-gateway"
DELEGATION_TOKEN_TYPE = "agent-delegation+jwt"


@dataclass(frozen=True)
class DelegationDecision:
    allowed: bool
    reason: str
    reason_code: str
    claims: dict[str, Any]

    def to_audit_event(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "reason_code": self.reason_code,
            "grant_id": self.claims.get("grant_id"),
            "principal_id": self.claims.get("sub"),
            "agent_id": self.claims.get("agent_id"),
            "purpose": self.claims.get("purpose"),
            "expires_at": self.claims.get("exp"),
        }


def issue_delegation_token(
    *,
    grant_id: str,
    principal_id: str,
    principal_role: str,
    tenant_id: str,
    agent_id: str,
    allowed_tools: Sequence[str],
    resource_scopes: Mapping[str, Any] | None = None,
    constraints: Mapping[str, Any] | None = None,
    purpose: str = "",
    approval_id: str | None = None,
    expires_at: datetime | None = None,
) -> tuple[str, str, datetime]:
    """Create a signed capability token and return token, JTI hash and expiry."""

    settings = get_settings()
    now = datetime.now(timezone.utc)
    expiry = expires_at or now + timedelta(minutes=settings.agent_delegation_token_minutes)
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    if expiry <= now:
        raise ValueError("delegation expiry must be in the future")
    jti = secrets.token_urlsafe(24)
    payload = {
        "iss": DELEGATION_ISSUER,
        "aud": DELEGATION_AUDIENCE,
        "typ": DELEGATION_TOKEN_TYPE,
        "jti": jti,
        "grant_id": grant_id,
        "sub": str(principal_id),
        "role": str(principal_role).upper(),
        "tenant_id": tenant_id,
        "agent_id": agent_id,
        "allowed_tools": sorted({str(item) for item in allowed_tools}),
        "resource_scopes": dict(resource_scopes or {}),
        "constraints": dict(constraints or {}),
        "purpose": purpose,
        "approval_id": approval_id,
        "iat": now,
        "nbf": now,
        "exp": expiry,
    }
    token = jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)
    return token, _hash_jti(jti), expiry


def decode_delegation_token(token: str) -> dict[str, Any]:
    settings = get_settings()
    payload = jwt.decode(
        token,
        settings.secret_key,
        algorithms=[settings.jwt_algorithm],
        audience=DELEGATION_AUDIENCE,
        issuer=DELEGATION_ISSUER,
    )
    if payload.get("typ") != DELEGATION_TOKEN_TYPE:
        raise JWTError("unexpected delegation token type")
    return dict(payload)


def authorize_delegated_tool(
    token: str | None,
    *,
    required: bool,
    principal_id: str,
    principal_role: str,
    tenant_id: str,
    agent_id: str,
    specialist_id: str | None,
    tool_name: str,
    args: Mapping[str, Any],
    approval_id: str | None,
) -> DelegationDecision:
    if not token:
        return DelegationDecision(
            allowed=not required,
            reason="Delegation token is not required for this execution mode." if not required else "A delegation token is required for this write operation.",
            reason_code="DELEGATION_NOT_REQUIRED" if not required else "DELEGATION_REQUIRED",
            claims={},
        )
    try:
        claims = decode_delegation_token(token)
    except JWTError as exc:
        return DelegationDecision(False, f"Delegation token is invalid: {exc}", "DELEGATION_INVALID", {})

    checks = (
        (str(claims.get("sub")) == str(principal_id), "Delegation principal does not match the requester.", "DELEGATION_PRINCIPAL_MISMATCH"),
        (str(claims.get("role") or "").upper() == str(principal_role or "").upper(), "Delegation role does not match the requester role.", "DELEGATION_ROLE_MISMATCH"),
        (str(claims.get("tenant_id")) == str(tenant_id), "Delegation tenant does not match the execution tenant.", "DELEGATION_TENANT_MISMATCH"),
        (str(claims.get("agent_id")) == str(agent_id), "Delegation is bound to another Agent identity.", "DELEGATION_AGENT_MISMATCH"),
    )
    for allowed, reason, code in checks:
        if not allowed:
            return DelegationDecision(False, reason, code, claims)

    allowed_tools = {str(item) for item in claims.get("allowed_tools") or []}
    if tool_name not in allowed_tools and "*" not in allowed_tools:
        return DelegationDecision(False, f"Tool '{tool_name}' is outside the delegated tool set.", "DELEGATION_TOOL_DENIED", claims)

    constraints = dict(claims.get("constraints") or {})
    specialists = {str(item) for item in constraints.get("allowed_specialists") or []}
    if specialist_id and specialists and specialist_id not in specialists:
        return DelegationDecision(False, f"Specialist '{specialist_id}' is outside the delegation.", "DELEGATION_SPECIALIST_DENIED", claims)

    token_approval = claims.get("approval_id")
    if token_approval and str(token_approval) != str(approval_id or ""):
        return DelegationDecision(False, "Delegation is bound to a different approval evidence record.", "DELEGATION_APPROVAL_MISMATCH", claims)

    scope_error = _resource_scope_error(dict(claims.get("resource_scopes") or {}), args)
    if scope_error:
        return DelegationDecision(False, scope_error, "DELEGATION_RESOURCE_DENIED", claims)
    constraint_error = _constraint_error(constraints, args)
    if constraint_error:
        return DelegationDecision(False, constraint_error, "DELEGATION_CONSTRAINT_DENIED", claims)
    return DelegationDecision(True, "Delegated authority verified.", "DELEGATION_ALLOWED", claims)


async def create_persisted_delegation(
    session: AsyncSession,
    *,
    tenant_id: str,
    principal_id: str,
    principal_role: str,
    issued_by: str,
    agent_id: str,
    allowed_tools: Sequence[str],
    resource_scopes: Mapping[str, Any] | None = None,
    constraints: Mapping[str, Any] | None = None,
    purpose: str = "",
    approval_id: str | None = None,
    expires_at: datetime | None = None,
    max_uses: int | None = None,
) -> tuple[AgentDelegationGrant, str]:
    grant_id = f"dlg_{uuid.uuid4().hex}"
    token, jti_hash, expiry = issue_delegation_token(
        grant_id=grant_id,
        principal_id=principal_id,
        principal_role=principal_role,
        tenant_id=tenant_id,
        agent_id=agent_id,
        allowed_tools=allowed_tools,
        resource_scopes=resource_scopes,
        constraints=constraints,
        purpose=purpose,
        approval_id=approval_id,
        expires_at=expires_at,
    )
    grant = AgentDelegationGrant(
        grant_id=grant_id,
        tenant_id=tenant_id,
        principal_id=str(principal_id),
        principal_role=str(principal_role).upper(),
        issued_by=str(issued_by),
        agent_id=agent_id,
        allowed_tools=list(allowed_tools),
        resource_scopes=dict(resource_scopes or {}),
        constraints=dict(constraints or {}),
        purpose=purpose,
        approval_id=approval_id,
        status="active",
        token_jti_hash=jti_hash,
        expires_at=expiry.replace(tzinfo=None),
        max_uses=max_uses or get_settings().agent_delegation_default_max_uses,
    )
    session.add(grant)
    await session.flush()
    return grant, token


async def consume_persisted_delegation(
    session: AsyncSession,
    token: str,
    *,
    principal_id: str,
    tenant_id: str,
) -> AgentDelegationGrant:
    """Atomically consume one task-level use of a durable delegation grant."""

    claims = decode_delegation_token(token)
    if str(claims.get("sub")) != str(principal_id) or str(claims.get("tenant_id")) != str(tenant_id):
        raise ValueError("delegation principal or tenant mismatch")
    grant = await session.scalar(
        select(AgentDelegationGrant)
        .where(
            AgentDelegationGrant.grant_id == str(claims.get("grant_id")),
            AgentDelegationGrant.tenant_id == tenant_id,
        )
        .with_for_update()
    )
    if grant is None or grant.token_jti_hash != _hash_jti(str(claims.get("jti") or "")):
        raise ValueError("delegation grant was not found")
    now = datetime.utcnow()
    if grant.status != "active" or grant.expires_at <= now:
        raise ValueError("delegation grant is revoked or expired")
    if grant.use_count >= grant.max_uses:
        raise ValueError("delegation grant use limit is exhausted")
    grant.use_count += 1
    grant.last_used_at = now
    await session.flush()
    return grant


def serialize_delegation(grant: AgentDelegationGrant) -> dict[str, Any]:
    return {
        "grant_id": grant.grant_id,
        "tenant_id": grant.tenant_id,
        "principal_id": grant.principal_id,
        "principal_role": grant.principal_role,
        "issued_by": grant.issued_by,
        "agent_id": grant.agent_id,
        "allowed_tools": list(grant.allowed_tools or []),
        "resource_scopes": dict(grant.resource_scopes or {}),
        "constraints": dict(grant.constraints or {}),
        "purpose": grant.purpose,
        "approval_id": grant.approval_id,
        "status": grant.status,
        "expires_at": grant.expires_at.isoformat(),
        "max_uses": grant.max_uses,
        "use_count": grant.use_count,
        "last_used_at": grant.last_used_at.isoformat() if grant.last_used_at else None,
        "created_at": grant.created_at.isoformat(),
        "revoked_at": grant.revoked_at.isoformat() if grant.revoked_at else None,
    }


def _hash_jti(jti: str) -> str:
    return hashlib.sha256(jti.encode("utf-8")).hexdigest()


def _resource_scope_error(scopes: Mapping[str, Any], args: Mapping[str, Any]) -> str | None:
    for field_name, allowed_values in scopes.items():
        if field_name not in args:
            continue
        values = allowed_values if isinstance(allowed_values, list) else [allowed_values]
        if "*" not in values and str(args[field_name]) not in {str(item) for item in values}:
            return f"Resource '{field_name}={args[field_name]}' is outside the delegated scope."
    return None


def _constraint_error(constraints: Mapping[str, Any], args: Mapping[str, Any]) -> str | None:
    amount = next((args.get(key) for key in ("amount", "refund_amount", "requested_amount") if args.get(key) is not None), None)
    if amount is not None and constraints.get("max_amount") is not None:
        try:
            if float(amount) > float(constraints["max_amount"]):
                return "Requested amount exceeds the delegated maximum."
        except (TypeError, ValueError):
            return "Requested amount cannot be validated against the delegation."
    currencies = {str(item).upper() for item in constraints.get("allowed_currencies") or []}
    if currencies and str(args.get("currency") or "").upper() not in currencies:
        return "Requested currency is outside the delegated currency set."
    return None
