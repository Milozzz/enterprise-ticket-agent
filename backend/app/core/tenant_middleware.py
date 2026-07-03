"""Resolve a trusted tenant identity and bind it to the request context."""

from __future__ import annotations

import re

from fastapi import Request
from jose import JWTError, jwt
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, Response

from app.core.config import get_settings
from app.db.tenant_context import tenant_scope


TENANT_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,49}$")


def _jwt_tenant(request: Request) -> str | None:
    authorization = request.headers.get("Authorization", "")
    if not authorization.startswith("Bearer "):
        return None
    settings = get_settings()
    try:
        payload = jwt.decode(
            authorization[7:],
            settings.secret_key,
            algorithms=[settings.jwt_algorithm],
        )
    except JWTError:
        return None
    value = payload.get("tenant_id")
    return str(value) if value else None


class TenantContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        settings = get_settings()
        token_tenant = _jwt_tenant(request)
        header_tenant = request.headers.get("X-Tenant-ID")

        if header_tenant and not TENANT_PATTERN.fullmatch(header_tenant):
            return JSONResponse(status_code=400, content={"detail": "Invalid X-Tenant-ID"})

        if settings.environment == "production" and header_tenant:
            if token_tenant is None:
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Tenant override requires an authenticated token"},
                )
            if header_tenant != token_tenant:
                return JSONResponse(
                    status_code=403,
                    content={"detail": "Tenant header does not match authenticated tenant"},
                )

        tenant_id = token_tenant or header_tenant or settings.default_tenant_id
        if not TENANT_PATTERN.fullmatch(tenant_id):
            return JSONResponse(status_code=400, content={"detail": "Invalid tenant identity"})

        request.state.tenant_id = tenant_id
        with tenant_scope(tenant_id):
            response = await call_next(request)
        response.headers["X-Tenant-ID"] = tenant_id
        return response
