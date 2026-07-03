"""Canonical business-object identity resolution.

External identifiers remain accepted at API boundaries, while workflow state,
foreign keys, idempotency keys, and SAP calls use a single canonical ID.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import BusinessObjectAlias
from app.db.tenant_context import current_tenant_id

LEGACY_ORDER_ALIASES: dict[str, str] = {
    "123456": "ERP-ORD-1001",
    "789012": "ERP-ORD-1002",
    "456789": "ERP-ORD-1003",
}


@dataclass(frozen=True)
class ResolvedBusinessId:
    requested_id: str
    canonical_id: str
    source_system: str
    alias_used: bool


def alias_record_id(
    *, tenant_id: str, object_type: str, source_system: str, external_id: str
) -> str:
    raw = f"{tenant_id}:{object_type}:{source_system}:{external_id}"
    return f"ALIAS-{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:24].upper()}"


async def resolve_business_id(
    session: AsyncSession,
    *,
    object_type: str,
    external_id: str,
    tenant_id: str | None = None,
) -> ResolvedBusinessId:
    tenant_id = tenant_id or current_tenant_id()
    normalized = external_id.strip()
    alias = await session.scalar(
        select(BusinessObjectAlias).where(
            BusinessObjectAlias.tenant_id == tenant_id,
            BusinessObjectAlias.object_type == object_type,
            BusinessObjectAlias.external_id == normalized,
            BusinessObjectAlias.active.is_(True),
        )
    )
    if alias is None:
        return ResolvedBusinessId(
            requested_id=normalized,
            canonical_id=normalized,
            source_system="CANONICAL",
            alias_used=False,
        )
    return ResolvedBusinessId(
        requested_id=normalized,
        canonical_id=alias.canonical_id,
        source_system=alias.source_system,
        alias_used=alias.canonical_id != normalized,
    )


async def resolve_order_id(
    session: AsyncSession,
    order_id: str,
    *,
    tenant_id: str | None = None,
) -> ResolvedBusinessId:
    return await resolve_business_id(
        session,
        object_type="ORDER",
        external_id=order_id,
        tenant_id=tenant_id,
    )


async def seed_legacy_order_aliases(
    session: AsyncSession,
    *,
    tenant_id: str | None = None,
) -> None:
    tenant_id = tenant_id or current_tenant_id()
    for external_id, canonical_id in LEGACY_ORDER_ALIASES.items():
        await session.merge(
            BusinessObjectAlias(
                alias_id=alias_record_id(
                    tenant_id=tenant_id,
                    object_type="ORDER",
                    source_system="LEGACY_DEMO",
                    external_id=external_id,
                ),
                tenant_id=tenant_id,
                object_type="ORDER",
                source_system="LEGACY_DEMO",
                external_id=external_id,
                canonical_id=canonical_id,
                alias_metadata={"migration": "canonical_order_v1"},
                active=True,
            )
        )
