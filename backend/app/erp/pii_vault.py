"""PII lifecycle service: encryption, purpose binding, retention, deletion."""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.field_encryption import (
    EnvelopeFieldCipher,
    EnvironmentKeyProvider,
    HTTPKMSKeyProvider,
    LocalKMSKeyProvider,
)
from app.db.models import PIIRecord


def _record_id(tenant_id: str, subject_type: str, subject_id: str, field_name: str, purpose: str) -> str:
    # purpose 纳入主键：同一 (tenant,subject,field) 的不同用途各存一行，避免换 purpose 时
    # merge 覆盖旧记录、且旧 purpose 读不到（静默丢数据）。与 AAD 的维度保持一致。
    value = f"{tenant_id}:{subject_type}:{subject_id}:{field_name}:{purpose}"
    return f"PII-{hashlib.sha256(value.encode()).hexdigest()[:28].upper()}"


def _aad(tenant_id: str, subject_type: str, subject_id: str, field_name: str, purpose: str) -> bytes:
    return f"{tenant_id}|{subject_type}|{subject_id}|{field_name}|{purpose}".encode()


def _cipher() -> EnvelopeFieldCipher:
    settings = get_settings()
    if settings.kms_provider == "http":
        provider = HTTPKMSKeyProvider(settings.kms_endpoint, settings.kms_bearer_token)
    elif settings.kms_provider == "local":
        provider = LocalKMSKeyProvider(EnvironmentKeyProvider(settings.field_encryption_key))
    else:
        raise RuntimeError(f"unsupported KMS_PROVIDER: {settings.kms_provider}")
    return EnvelopeFieldCipher(provider, settings.field_encryption_key_id)


async def put_pii(
    session: AsyncSession,
    *,
    tenant_id: str,
    subject_type: str,
    subject_id: str,
    field_name: str,
    value: str,
    purpose: str,
    legal_basis: str | None = None,
    retention_days: int | None = None,
) -> PIIRecord:
    settings = get_settings()
    expires_at = datetime.utcnow() + timedelta(
        days=retention_days or settings.pii_default_retention_days
    )
    aad = _aad(tenant_id, subject_type, subject_id, field_name, purpose)
    encrypted = _cipher().encrypt(value.encode(), aad=aad)
    record = PIIRecord(
        pii_record_id=_record_id(tenant_id, subject_type, subject_id, field_name, purpose),
        tenant_id=tenant_id,
        subject_type=subject_type,
        subject_id=subject_id,
        field_name=field_name,
        ciphertext=encrypted.ciphertext,
        encrypted_data_key=encrypted.encrypted_data_key,
        key_id=settings.field_encryption_key_id,
        purpose=purpose,
        legal_basis=legal_basis,
        expires_at=expires_at,
        deleted_at=None,
    )
    return await session.merge(record)


async def get_pii(
    session: AsyncSession,
    *,
    tenant_id: str,
    subject_type: str,
    subject_id: str,
    field_name: str,
    purpose: str,
) -> str | None:
    record = await session.scalar(
        select(PIIRecord).where(
            PIIRecord.tenant_id == tenant_id,
            PIIRecord.subject_type == subject_type,
            PIIRecord.subject_id == subject_id,
            PIIRecord.field_name == field_name,
            PIIRecord.purpose == purpose,
            PIIRecord.deleted_at.is_(None),
        )
    )
    if record is None or record.expires_at <= datetime.utcnow() or record.purpose != purpose:
        return None
    aad = _aad(tenant_id, subject_type, subject_id, field_name, purpose)
    return _cipher().decrypt(
        record.ciphertext,
        encrypted_data_key=record.encrypted_data_key,
        aad=aad,
    ).decode()


async def delete_subject_pii(
    session: AsyncSession, *, tenant_id: str, subject_type: str, subject_id: str
) -> int:
    records = (
        await session.execute(
            select(PIIRecord).where(
                PIIRecord.tenant_id == tenant_id,
                PIIRecord.subject_type == subject_type,
                PIIRecord.subject_id == subject_id,
                PIIRecord.deleted_at.is_(None),
            )
        )
    ).scalars().all()
    now = datetime.utcnow()
    for record in records:
        record.ciphertext = b""
        record.encrypted_data_key = b""
        record.deleted_at = now
    return len(records)


async def purge_expired_pii(
    session: AsyncSession,
    *,
    tenant_id: str | None = None,
    now: datetime | None = None,
) -> int:
    cutoff = now or datetime.utcnow()
    conditions = [
        PIIRecord.expires_at <= cutoff,
        PIIRecord.deleted_at.is_(None),
    ]
    if tenant_id is not None:
        conditions.append(PIIRecord.tenant_id == tenant_id)
    records = (
        await session.execute(
            select(PIIRecord).where(*conditions)
        )
    ).scalars().all()
    for record in records:
        record.ciphertext = b""
        record.encrypted_data_key = b""
        record.deleted_at = cutoff
    return len(records)
