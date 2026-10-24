"""Encryption at rest for sensitive client data: intake answers (bank and FBAR account numbers, SSN/TIN,
e-file PINs) in the database, and stored files (tax documents, chat attachments, payment proofs) on disk.

Values are encrypted with Fernet (AES-128-CBC + HMAC-SHA256) using DATA_ENCRYPTION_KEYS from .env:
a comma-separated list where the FIRST key encrypts and every key can decrypt, so a key can be rotated
by putting a new one first and keeping the old one until `alembic upgrade` / re-saving has moved the data.

Make a key with:  python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

LOSING THE KEY MEANS LOSING THE DATA. Keep a copy in Bitwarden, separate from database backups.
Without DATA_ENCRYPTION_KEYS (development only) a key derived from SECRET_KEY is used.
"""
from __future__ import annotations

import base64
import hashlib
import json
from functools import lru_cache
from typing import Any

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from sqlalchemy import JSON
from sqlalchemy.types import TypeDecorator

from app.core.config import settings

_MARK = "_enc"      # an encrypted value is stored as {"_enc": "v1", "ct": "<token>"}
_VERSION = "v1"


def _dev_key() -> bytes:
    return base64.urlsafe_b64encode(hashlib.sha256(("data-key:" + settings.SECRET_KEY).encode()).digest())


@lru_cache(maxsize=1)
def _fernet() -> MultiFernet:
    keys = [Fernet(k.strip().encode()) for k in settings.DATA_ENCRYPTION_KEYS.split(",") if k.strip()]
    if not settings.is_production:
        keys.append(Fernet(_dev_key()))  # development data saved before a key was set stays readable
    return MultiFernet(keys)


def encrypt_json(value: Any) -> dict:
    token = _fernet().encrypt(json.dumps(value, separators=(",", ":")).encode())
    return {_MARK: _VERSION, "ct": token.decode()}


def is_encrypted(stored: Any) -> bool:
    return isinstance(stored, dict) and stored.get(_MARK) == _VERSION and "ct" in stored


def decrypt_json(stored: Any) -> Any:
    """The original value. Rows saved before encryption was added come back unchanged."""
    if not is_encrypted(stored):
        return stored
    try:
        return json.loads(_fernet().decrypt(stored["ct"].encode()))
    except InvalidToken as exc:
        raise RuntimeError("Stored data can't be decrypted: DATA_ENCRYPTION_KEYS doesn't have the key it was saved with.") from exc


# ── Stored files (client documents, chat attachments, payment proofs) ───────
# On disk an encrypted file is FILE_MAGIC + a Fernet token. Files without the marker (saved before
# encryption was added, or profile pictures, which are public) are read as they are.
FILE_MAGIC = b"CTENC1\n"


def encrypt_bytes(data: bytes) -> bytes:
    return FILE_MAGIC + _fernet().encrypt(data)


def is_encrypted_bytes(blob: bytes) -> bool:
    return blob.startswith(FILE_MAGIC)


def decrypt_bytes(blob: bytes) -> bytes:
    if not is_encrypted_bytes(blob):
        return blob
    try:
        return _fernet().decrypt(blob[len(FILE_MAGIC):])
    except InvalidToken as exc:
        raise RuntimeError("Stored file can't be decrypted: DATA_ENCRYPTION_KEYS doesn't have the key it was saved with.") from exc


class EncryptedJSON(TypeDecorator):
    """A JSON column whose whole value is encrypted in the database and plain in Python."""

    impl = JSON
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return None if value is None else encrypt_json(value)

    def process_result_value(self, value, dialect):
        return None if value is None else decrypt_json(value)
