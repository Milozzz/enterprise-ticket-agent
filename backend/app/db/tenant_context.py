"""Tenant context propagation for application queries and PostgreSQL RLS."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


DEFAULT_TENANT_ID = "TENANT-DEMO-COMMERCE"
_tenant_id: ContextVar[str] = ContextVar("tenant_id", default=DEFAULT_TENANT_ID)


def current_tenant_id() -> str:
    return _tenant_id.get()


@contextmanager
def tenant_scope(tenant_id: str):
    if not tenant_id or len(tenant_id) > 50:
        raise ValueError("invalid tenant_id")
    token = _tenant_id.set(tenant_id)
    try:
        yield
    finally:
        _tenant_id.reset(token)


async def apply_tenant_context(session: AsyncSession, tenant_id: str | None = None) -> str:
    """Set the transaction-local tenant consumed by PostgreSQL RLS policies."""

    resolved = tenant_id or current_tenant_id()
    bind = session.get_bind()
    if bind.dialect.name == "postgresql":
        await session.execute(
            text("SELECT set_config('app.current_tenant', :tenant_id, true)"),
            {"tenant_id": resolved},
        )
    return resolved
