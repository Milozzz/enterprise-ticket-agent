"""Envelope-style application encryption with replaceable key providers."""

from __future__ import annotations

import base64
import hashlib
import os
from dataclasses import dataclass
from typing import Protocol

import httpx
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class KeyProvider(Protocol):
    def get_key(self, key_id: str) -> bytes: ...


@dataclass(frozen=True)
class EnvironmentKeyProvider:
    secret: str

    def get_key(self, key_id: str) -> bytes:
        if not self.secret:
            raise RuntimeError("FIELD_ENCRYPTION_KEY is required")
        try:
            decoded = base64.urlsafe_b64decode(self.secret + "=" * (-len(self.secret) % 4))
        except ValueError:
            decoded = b""
        if len(decoded) not in (16, 24, 32):
            decoded = hashlib.sha256(f"{key_id}:{self.secret}".encode()).digest()
        return decoded


class FieldCipher:
    VERSION = b"v1"

    def __init__(self, provider: KeyProvider, key_id: str):
        self.provider = provider
        self.key_id = key_id

    def encrypt(self, plaintext: bytes, *, aad: bytes) -> bytes:
        nonce = os.urandom(12)
        encrypted = AESGCM(self.provider.get_key(self.key_id)).encrypt(nonce, plaintext, aad)
        return self.VERSION + nonce + encrypted

    def decrypt(self, payload: bytes, *, aad: bytes) -> bytes:
        if not payload.startswith(self.VERSION) or len(payload) < 15:
            raise ValueError("unsupported ciphertext envelope")
        nonce = payload[2:14]
        return AESGCM(self.provider.get_key(self.key_id)).decrypt(nonce, payload[14:], aad)


class EnvelopeKeyProvider(Protocol):
    def wrap_key(self, key_id: str, plaintext_key: bytes) -> bytes: ...
    def unwrap_key(self, key_id: str, wrapped_key: bytes) -> bytes: ...


@dataclass(frozen=True)
class LocalKMSKeyProvider:
    """Development KMS emulator; the master key never enters the database."""

    master_key_provider: KeyProvider

    def wrap_key(self, key_id: str, plaintext_key: bytes) -> bytes:
        nonce = os.urandom(12)
        master = self.master_key_provider.get_key(key_id)
        return b"kw1" + nonce + AESGCM(master).encrypt(nonce, plaintext_key, key_id.encode())

    def unwrap_key(self, key_id: str, wrapped_key: bytes) -> bytes:
        if not wrapped_key.startswith(b"kw1") or len(wrapped_key) < 16:
            raise ValueError("unsupported wrapped data key")
        nonce = wrapped_key[3:15]
        master = self.master_key_provider.get_key(key_id)
        return AESGCM(master).decrypt(nonce, wrapped_key[15:], key_id.encode())


@dataclass(frozen=True)
class HTTPKMSKeyProvider:
    """Adapter for a Vault/cloud-KMS gateway exposing wrap/unwrap endpoints."""

    endpoint: str
    bearer_token: str
    timeout_seconds: float = 5.0

    def _call(self, action: str, key_id: str, field: str, value: bytes) -> bytes:
        if not self.endpoint.startswith("https://"):
            raise RuntimeError("KMS_ENDPOINT must use HTTPS")
        response = httpx.post(
            f"{self.endpoint.rstrip('/')}/v1/keys/{key_id}:{action}",
            json={field: base64.b64encode(value).decode()},
            headers={"Authorization": f"Bearer {self.bearer_token}"},
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        result_field = "wrapped_key" if action == "wrap" else "plaintext_key"
        return base64.b64decode(response.json()[result_field])

    def wrap_key(self, key_id: str, plaintext_key: bytes) -> bytes:
        return self._call("wrap", key_id, "plaintext_key", plaintext_key)

    def unwrap_key(self, key_id: str, wrapped_key: bytes) -> bytes:
        return self._call("unwrap", key_id, "wrapped_key", wrapped_key)


@dataclass(frozen=True)
class EncryptedEnvelope:
    ciphertext: bytes
    encrypted_data_key: bytes


class EnvelopeFieldCipher:
    VERSION = b"v2"

    def __init__(self, provider: EnvelopeKeyProvider, key_id: str):
        self.provider = provider
        self.key_id = key_id

    def encrypt(self, plaintext: bytes, *, aad: bytes) -> EncryptedEnvelope:
        data_key = AESGCM.generate_key(bit_length=256)
        nonce = os.urandom(12)
        ciphertext = self.VERSION + nonce + AESGCM(data_key).encrypt(nonce, plaintext, aad)
        return EncryptedEnvelope(
            ciphertext=ciphertext,
            encrypted_data_key=self.provider.wrap_key(self.key_id, data_key),
        )

    def decrypt(self, payload: bytes, *, encrypted_data_key: bytes, aad: bytes) -> bytes:
        if not payload.startswith(self.VERSION) or len(payload) < 15:
            raise ValueError("unsupported ciphertext envelope")
        data_key = self.provider.unwrap_key(self.key_id, encrypted_data_key)
        nonce = payload[2:14]
        return AESGCM(data_key).decrypt(nonce, payload[14:], aad)
