"""Small database-backed schema registry and lineage catalog."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import DataContract, DataContractVersion, DataLineageEdge


class ContractValidationError(ValueError):
    pass


def schema_fingerprint(schema: dict[str, Any]) -> str:
    canonical = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def validate_payload(schema: dict[str, Any], payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    properties = schema.get("properties", {})
    for field in schema.get("required", []):
        if field not in payload or payload[field] is None:
            errors.append(f"missing required field: {field}")
    type_map = {
        "string": str,
        "integer": int,
        "number": (int, float),
        "boolean": bool,
        "object": dict,
        "array": list,
    }
    for field, value in payload.items():
        definition = properties.get(field)
        if definition is None:
            if schema.get("additionalProperties") is False:
                errors.append(f"unexpected field: {field}")
            continue
        expected = type_map.get(definition.get("type"))
        if expected and value is not None and not isinstance(value, expected):
            errors.append(f"field {field} must be {definition['type']}")
    return errors


def assert_backward_compatible(previous: dict[str, Any], candidate: dict[str, Any]) -> None:
    old_required = set(previous.get("required", []))
    new_required = set(candidate.get("required", []))
    newly_required = new_required - old_required
    if newly_required:
        raise ContractValidationError(
            f"backward compatibility forbids new required fields: {sorted(newly_required)}"
        )
    old_properties = previous.get("properties", {})
    new_properties = candidate.get("properties", {})
    removed = set(old_properties) - set(new_properties)
    if removed:
        raise ContractValidationError(f"backward compatibility forbids removed fields: {sorted(removed)}")
    changed = [
        name
        for name in old_properties.keys() & new_properties.keys()
        if old_properties[name].get("type") != new_properties[name].get("type")
    ]
    if changed:
        raise ContractValidationError(f"backward compatibility forbids type changes: {sorted(changed)}")


async def register_contract_version(
    session: AsyncSession,
    *,
    tenant_id: str,
    contract_name: str,
    object_type: str,
    owner: str,
    schema: dict[str, Any],
    created_by: str,
) -> DataContractVersion:
    contract = await session.scalar(
        select(DataContract).where(
            DataContract.tenant_id == tenant_id,
            DataContract.contract_name == contract_name,
        )
    )
    if contract is None:
        contract_id = f"CONTRACT-{hashlib.sha256(f'{tenant_id}:{contract_name}'.encode()).hexdigest()[:20].upper()}"
        contract = DataContract(
            contract_id=contract_id,
            tenant_id=tenant_id,
            contract_name=contract_name,
            object_type=object_type,
            owner=owner,
            active_version=0,
        )
        session.add(contract)
        await session.flush()
    if contract.active_version:
        current = await session.scalar(
            select(DataContractVersion).where(
                DataContractVersion.contract_id == contract.contract_id,
                DataContractVersion.version == contract.active_version,
            )
        )
        if current and contract.compatibility_mode == "BACKWARD":
            assert_backward_compatible(current.schema_definition, schema)
    next_version = contract.active_version + 1
    version = DataContractVersion(
        contract_version_id=f"{contract.contract_id}-V{next_version}",
        contract_id=contract.contract_id,
        version=next_version,
        schema_definition=schema,
        schema_fingerprint=schema_fingerprint(schema),
        status="ACTIVE",
        created_by=created_by,
        created_at=datetime.utcnow(),
    )
    session.add(version)
    contract.active_version = next_version
    await session.flush()
    return version


async def add_lineage_edge(
    session: AsyncSession,
    *,
    tenant_id: str,
    source_asset: str,
    target_asset: str,
    transformation: str,
    job_name: str | None = None,
    column_mapping: dict | None = None,
) -> DataLineageEdge:
    raw = f"{tenant_id}:{source_asset}:{target_asset}:{transformation}"
    edge = DataLineageEdge(
        lineage_edge_id=f"LINEAGE-{hashlib.sha256(raw.encode()).hexdigest()[:24].upper()}",
        tenant_id=tenant_id,
        source_asset=source_asset,
        target_asset=target_asset,
        transformation=transformation,
        job_name=job_name,
        column_mapping=column_mapping,
    )
    await session.merge(edge)
    return edge
