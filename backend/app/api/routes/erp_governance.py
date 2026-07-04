"""Authenticated data-governance and integration operations."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.core.auth import get_current_user
from app.db.database import AsyncSessionLocal
from app.db.models import (
    DataContract,
    DataContractVersion,
    DataLineageEdge,
    OutboxEvent,
)
from app.erp.cdc import record_cdc_change
from app.erp.evidence import export_evidence_bundle
from app.erp.outbox_worker import process_outbox_batch, replay_dead_letter, webhook_dispatcher
from app.erp.pii_vault import delete_subject_pii, get_pii, purge_expired_pii, put_pii
from app.erp.reconciliation import reconcile_snapshots
from app.erp.schema_registry import (
    ContractValidationError,
    add_lineage_edge,
    register_contract_version,
    validate_payload,
)


router = APIRouter()


class PIIWritePayload(BaseModel):
    subject_type: str = Field(min_length=1, max_length=80)
    subject_id: str = Field(min_length=1, max_length=120)
    field_name: str = Field(min_length=1, max_length=80)
    value: str = Field(min_length=1, max_length=10000)
    purpose: str = Field(min_length=1, max_length=120)
    legal_basis: str | None = Field(default=None, max_length=120)
    retention_days: int | None = Field(default=None, ge=1, le=3650)


class CDCWritePayload(BaseModel):
    source_system: str
    stream_name: str
    source_position: str
    object_type: str
    object_id: str
    operation: str
    before_state: dict[str, Any] | None = None
    after_state: dict[str, Any] | None = None


class ReconciliationPayload(BaseModel):
    object_type: str
    object_id: str
    source_system: str
    target_system: str
    source_snapshot: dict[str, Any]
    target_snapshot: dict[str, Any]


class ContractPayload(BaseModel):
    contract_name: str
    object_type: str
    owner: str
    schema_definition: dict[str, Any]


class ContractValidationPayload(BaseModel):
    payload: dict[str, Any]
    version: int | None = None


class LineagePayload(BaseModel):
    source_asset: str
    target_asset: str
    transformation: str
    job_name: str | None = None
    column_mapping: dict[str, str] | None = None


def _tenant(user: dict) -> str:
    return str(user.get("tenant_id") or "")


def _require_roles(user: dict, *roles: str) -> None:
    if str(user.get("role") or "USER").upper() not in set(roles):
        raise HTTPException(status_code=403, detail=f"Required role: {', '.join(roles)}")


@router.get("/evidence/{saga_id}")
async def get_evidence_bundle(
    saga_id: str,
    user: Annotated[dict, Depends(get_current_user)],
) -> dict[str, Any]:
    _require_roles(user, "MANAGER", "FINANCE", "SECURITY", "ADMIN")
    async with AsyncSessionLocal() as session:
        bundle = await export_evidence_bundle(
            session,
            tenant_id=_tenant(user),
            saga_id=saga_id,
        )
    if bundle is None:
        raise HTTPException(status_code=404, detail="Saga evidence bundle not found")
    return bundle


@router.post("/pii")
async def store_pii(
    payload: PIIWritePayload,
    user: Annotated[dict, Depends(get_current_user)],
) -> dict[str, Any]:
    _require_roles(user, "AGENT", "MANAGER", "SECURITY", "ADMIN")
    async with AsyncSessionLocal() as session:
        record = await put_pii(
            session,
            tenant_id=_tenant(user),
            subject_type=payload.subject_type,
            subject_id=payload.subject_id,
            field_name=payload.field_name,
            value=payload.value,
            purpose=payload.purpose,
            legal_basis=payload.legal_basis,
            retention_days=payload.retention_days,
        )
        await session.commit()
        return {
            "pii_record_id": record.pii_record_id,
            "key_id": record.key_id,
            "expires_at": record.expires_at.isoformat(),
        }


@router.get("/pii/{subject_type}/{subject_id}/{field_name}")
async def read_pii(
    subject_type: str,
    subject_id: str,
    field_name: str,
    purpose: Annotated[str, Query(min_length=1, max_length=120)],
    user: Annotated[dict, Depends(get_current_user)],
) -> dict[str, Any]:
    _require_roles(user, "SECURITY", "MANAGER", "ADMIN")
    async with AsyncSessionLocal() as session:
        value = await get_pii(
            session,
            tenant_id=_tenant(user),
            subject_type=subject_type,
            subject_id=subject_id,
            field_name=field_name,
            purpose=purpose,
        )
    if value is None:
        raise HTTPException(status_code=404, detail="PII value not found, expired, or purpose denied")
    return {"value": value, "purpose": purpose}


@router.delete("/pii/{subject_type}/{subject_id}")
async def erase_subject_pii(
    subject_type: str,
    subject_id: str,
    user: Annotated[dict, Depends(get_current_user)],
) -> dict[str, int]:
    _require_roles(user, "SECURITY", "ADMIN")
    async with AsyncSessionLocal() as session:
        count = await delete_subject_pii(
            session,
            tenant_id=_tenant(user),
            subject_type=subject_type,
            subject_id=subject_id,
        )
        await session.commit()
    return {"deleted": count}


@router.post("/pii/purge-expired")
async def purge_pii(user: Annotated[dict, Depends(get_current_user)]) -> dict[str, int]:
    _require_roles(user, "SECURITY", "ADMIN")
    async with AsyncSessionLocal() as session:
        count = await purge_expired_pii(session, tenant_id=_tenant(user))
        await session.commit()
    return {"purged": count}


@router.get("/outbox")
async def list_outbox(
    user: Annotated[dict, Depends(get_current_user)],
    limit: int = Query(default=50, ge=1, le=500),
) -> dict[str, Any]:
    _require_roles(user, "MANAGER", "SECURITY", "ADMIN")
    async with AsyncSessionLocal() as session:
        rows = (
            await session.execute(
                select(OutboxEvent)
                .where(OutboxEvent.tenant_id == _tenant(user))
                .order_by(OutboxEvent.created_at.desc())
                .limit(limit)
            )
        ).scalars().all()
    return {
        "items": [
            {
                "event_id": row.outbox_event_id,
                "event_type": row.event_type,
                "aggregate_id": row.aggregate_id,
                "status": row.status.value,
                "attempts": row.attempts,
                "last_error": row.last_error,
                "replay_count": row.replay_count,
            }
            for row in rows
        ]
    }


@router.post("/outbox/dispatch")
async def dispatch_outbox(user: Annotated[dict, Depends(get_current_user)]) -> dict[str, int]:
    _require_roles(user, "SECURITY", "ADMIN")
    async with AsyncSessionLocal() as session:
        async def dispatch(event: OutboxEvent) -> None:
            await webhook_dispatcher(session, event)

        return await process_outbox_batch(
            session,
            worker_id=f"api:{user.get('user_id')}",
            dispatcher=dispatch,
            tenant_id=_tenant(user),
        )


@router.post("/outbox/{event_id}/replay")
async def replay_outbox(
    event_id: str,
    user: Annotated[dict, Depends(get_current_user)],
) -> dict[str, Any]:
    _require_roles(user, "SECURITY", "ADMIN")
    async with AsyncSessionLocal() as session:
        event = await session.get(OutboxEvent, event_id)
        if event is None or event.tenant_id != _tenant(user):
            raise HTTPException(status_code=404, detail="Outbox event not found")
        try:
            event = await replay_dead_letter(
                session,
                event_id=event_id,
                replayed_by=str(user.get("user_id")),
                tenant_id=_tenant(user),
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"event_id": event.outbox_event_id, "status": event.status.value}


@router.post("/cdc")
async def ingest_cdc(
    payload: CDCWritePayload,
    user: Annotated[dict, Depends(get_current_user)],
) -> dict[str, str]:
    _require_roles(user, "SECURITY", "ADMIN")
    async with AsyncSessionLocal() as session:
        event = await record_cdc_change(session, tenant_id=_tenant(user), **payload.model_dump())
        await session.commit()
    return {"cdc_event_id": event.cdc_event_id}


@router.post("/reconciliation")
async def reconcile(
    payload: ReconciliationPayload,
    user: Annotated[dict, Depends(get_current_user)],
) -> dict[str, Any]:
    _require_roles(user, "MANAGER", "FINANCE", "SECURITY", "ADMIN")
    async with AsyncSessionLocal() as session:
        issue = await reconcile_snapshots(
            session, tenant_id=_tenant(user), **payload.model_dump()
        )
        await session.commit()
    return {
        "matched": issue is None,
        "issue_id": issue.reconciliation_issue_id if issue else None,
    }


@router.post("/contracts")
async def register_contract(
    payload: ContractPayload,
    user: Annotated[dict, Depends(get_current_user)],
) -> dict[str, Any]:
    _require_roles(user, "SECURITY", "ADMIN")
    async with AsyncSessionLocal() as session:
        try:
            version = await register_contract_version(
                session,
                tenant_id=_tenant(user),
                contract_name=payload.contract_name,
                object_type=payload.object_type,
                owner=payload.owner,
                schema=payload.schema_definition,
                created_by=str(user.get("user_id")),
            )
            await session.commit()
        except ContractValidationError as exc:
            await session.rollback()
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "contract_id": version.contract_id,
        "version": version.version,
        "fingerprint": version.schema_fingerprint,
    }


@router.post("/contracts/{contract_id}/validate")
async def validate_contract_payload(
    contract_id: str,
    payload: ContractValidationPayload,
    user: Annotated[dict, Depends(get_current_user)],
) -> dict[str, Any]:
    async with AsyncSessionLocal() as session:
        contract = await session.get(DataContract, contract_id)
        if contract is None or contract.tenant_id != _tenant(user):
            raise HTTPException(status_code=404, detail="Data contract not found")
        version_number = payload.version or contract.active_version
        version = await session.scalar(
            select(DataContractVersion).where(
                DataContractVersion.contract_id == contract_id,
                DataContractVersion.version == version_number,
            )
        )
        if version is None:
            raise HTTPException(status_code=404, detail="Contract version not found")
        errors = validate_payload(version.schema_definition, payload.payload)
    return {"valid": not errors, "errors": errors, "version": version_number}


@router.post("/lineage")
async def create_lineage(
    payload: LineagePayload,
    user: Annotated[dict, Depends(get_current_user)],
) -> dict[str, str]:
    _require_roles(user, "SECURITY", "ADMIN")
    async with AsyncSessionLocal() as session:
        edge = await add_lineage_edge(
            session, tenant_id=_tenant(user), **payload.model_dump()
        )
        await session.commit()
    return {"lineage_edge_id": edge.lineage_edge_id}


@router.get("/lineage")
async def list_lineage(user: Annotated[dict, Depends(get_current_user)]) -> dict[str, Any]:
    async with AsyncSessionLocal() as session:
        rows = (
            await session.execute(
                select(DataLineageEdge).where(DataLineageEdge.tenant_id == _tenant(user))
            )
        ).scalars().all()
    return {
        "items": [
            {
                "lineage_edge_id": row.lineage_edge_id,
                "source_asset": row.source_asset,
                "target_asset": row.target_asset,
                "transformation": row.transformation,
                "job_name": row.job_name,
            }
            for row in rows
        ]
    }
