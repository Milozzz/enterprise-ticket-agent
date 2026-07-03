"""Commercial tenant onboarding and operations APIs."""

from __future__ import annotations

import hmac
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.commercial.operations import commercial_snapshot, provision_tenant, update_onboarding
from app.core.auth import get_optional_user
from app.core.config import get_settings
from app.db.database import AsyncSessionLocal
from app.db.models import TenantSubscription
from app.db.tenant_context import tenant_scope


router = APIRouter()


async def _commercial_user(
    user: Annotated[dict | None, Depends(get_optional_user)],
    admin_api_key: Annotated[str | None, Header(alias="X-Admin-API-Key")] = None,
) -> dict:
    settings = get_settings()
    if (
        settings.admin_api_key
        and admin_api_key
        and hmac.compare_digest(settings.admin_api_key, admin_api_key)
    ):
        return {
            "user_id": "admin-service",
            "role": "ADMIN",
            "tenant_id": settings.default_tenant_id,
        }
    if user:
        return user
    if settings.environment == "development":
        return {
            "user_id": "local-admin",
            "role": "ADMIN",
            "tenant_id": settings.default_tenant_id,
        }
    raise HTTPException(status_code=401, detail="Authentication is required")


class ProvisionTenantPayload(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    industry: str = Field(default="ecommerce", min_length=2, max_length=80)
    region: str = Field(default="CN", min_length=2, max_length=60)
    plan_code: str = Field(default="PILOT", min_length=2, max_length=50)
    monthly_action_quota: int = Field(default=1000, ge=10, le=10_000_000)


class OnboardingUpdatePayload(BaseModel):
    checklist: dict[str, bool] = Field(default_factory=dict)
    environment: str | None = Field(default=None, pattern="^(sandbox|staging|production)$")
    primary_connector_id: str | None = Field(default=None, max_length=100)


class SubscriptionUpdatePayload(BaseModel):
    plan_code: str = Field(min_length=2, max_length=50)
    monthly_action_quota: int = Field(ge=10, le=10_000_000)
    status: str = Field(default="ACTIVE", pattern="^(TRIAL|ACTIVE|PAST_DUE|SUSPENDED|CANCELED)$")


def _tenant(user: dict) -> str:
    tenant_id = str(user.get("tenant_id") or "").strip()
    if not tenant_id:
        raise HTTPException(status_code=403, detail="A tenant-scoped identity is required")
    return tenant_id


def _require_admin(user: dict) -> None:
    if str(user.get("role") or "USER").upper() not in {"MANAGER", "FINANCE", "SECURITY", "ADMIN"}:
        raise HTTPException(status_code=403, detail="Tenant administration role is required")


@router.post("/onboarding/provision")
async def provision_current_tenant(
    payload: ProvisionTenantPayload,
    user: Annotated[dict, Depends(_commercial_user)],
) -> dict[str, Any]:
    _require_admin(user)
    tenant_id = _tenant(user)
    with tenant_scope(tenant_id):
        async with AsyncSessionLocal() as session:
            result = await provision_tenant(
                session,
                tenant_id=tenant_id,
                name=payload.name,
                industry=payload.industry,
                region=payload.region,
                plan_code=payload.plan_code,
                monthly_action_quota=payload.monthly_action_quota,
            )
            await session.commit()
            return result


@router.patch("/onboarding")
async def patch_onboarding(
    payload: OnboardingUpdatePayload,
    user: Annotated[dict, Depends(_commercial_user)],
) -> dict[str, Any]:
    _require_admin(user)
    tenant_id = _tenant(user)
    with tenant_scope(tenant_id):
        async with AsyncSessionLocal() as session:
            try:
                onboarding = await update_onboarding(
                    session,
                    tenant_id=tenant_id,
                    updates=payload.checklist,
                    environment=payload.environment,
                    primary_connector_id=payload.primary_connector_id,
                )
                await session.commit()
            except LookupError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
            except ValueError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "tenant_id": tenant_id,
        "status": onboarding.status,
        "environment": onboarding.environment,
        "primary_connector_id": onboarding.primary_connector_id,
        "checklist": onboarding.checklist,
    }


@router.put("/subscription")
async def update_subscription(
    payload: SubscriptionUpdatePayload,
    user: Annotated[dict, Depends(_commercial_user)],
) -> dict[str, Any]:
    _require_admin(user)
    tenant_id = _tenant(user)
    with tenant_scope(tenant_id):
        async with AsyncSessionLocal() as session:
            subscription = await session.scalar(
                select(TenantSubscription).where(TenantSubscription.tenant_id == tenant_id)
            )
            if subscription is None:
                raise HTTPException(status_code=404, detail="Provision the tenant before changing its plan")
            subscription.plan_code = payload.plan_code.upper()
            subscription.monthly_action_quota = payload.monthly_action_quota
            subscription.status = payload.status
            await session.commit()
    return {
        "subscription_id": subscription.subscription_id,
        "plan_code": subscription.plan_code,
        "status": subscription.status,
        "monthly_action_quota": subscription.monthly_action_quota,
    }


@router.get("/operations/snapshot")
async def get_operations_snapshot(
    user: Annotated[dict, Depends(_commercial_user)],
) -> dict[str, Any]:
    _require_admin(user)
    tenant_id = _tenant(user)
    with tenant_scope(tenant_id):
        async with AsyncSessionLocal() as session:
            return await commercial_snapshot(session, tenant_id=tenant_id)
