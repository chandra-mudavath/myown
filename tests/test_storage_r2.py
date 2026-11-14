"""Uploaded files in Cloudflare R2 (STORAGE_BACKEND=r2): stored encrypted, served decrypted, missing files handled."""
from __future__ import annotations

import io

import pytest
from fastapi import UploadFile

from app.core.config import Settings
from app.core.crypto import is_encrypted_bytes
from app.core.errors import AppError
from app.core.files import send_file
from app.platform.services import payments
from app.platform.services import storage_provider as sp


class _S3Error(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.response = {"Error": {"Code": code}}


class FakeR2:
    """Stands in for the boto3 S3 client: put / get / delete objects in memory."""

    def __init__(self):
        self.objects: dict[tuple[str, str], bytes] = {}
        self.fail_with: str | None = None

    def put_object(self, Bucket, Key, Body, ContentType=None):
        self.objects[(Bucket, Key)] = Body

    def get_object(self, Bucket, Key):
        if self.fail_with:
            raise _S3Error(self.fail_with)
        if (Bucket, Key) not in self.objects:
            raise _S3Error("NoSuchKey")
        return {"Body": io.BytesIO(self.objects[(Bucket, Key)])}

    def delete_object(self, Bucket, Key):
        self.objects.pop((Bucket, Key), None)


@pytest.fixture
def r2(monkeypatch):
    fake = FakeR2()
    provider = sp.R2StorageProvider("https://acct.r2.cloudflarestorage.com", "covrenttax-documents", "id", "secret", client=fake)
    monkeypatch.setattr(sp, "storage_provider", provider)
    return provider, fake


def test_files_are_encrypted_in_the_bucket_and_come_back_as_uploaded(r2):
    provider, fake = r2
    key = provider.save(io.BytesIO(b"W-2 2025"), "storage\\client_documents\\C1\\2025\\F1\\W2\\w2_v1_ab12.pdf")

    assert key == "storage/client_documents/C1/2025/F1/W2/w2_v1_ab12.pdf"
    stored = fake.objects[("covrenttax-documents", key)]
    assert is_encrypted_bytes(stored) and b"W-2 2025" not in stored
    assert provider.open(key).read() == b"W-2 2025"

    provider.delete(key)
    with pytest.raises(FileNotFoundError):
        provider.open(key)


def test_bucket_errors_other_than_missing_are_not_hidden(r2):
    provider, fake = r2
    key = provider.save(io.BytesIO(b"x"), "private_storage/chat_attachments/T1/a.txt")
    fake.fail_with = "AccessDenied"
    with pytest.raises(_S3Error):
        provider.open(key)


def test_downloads_are_served_from_the_bucket(r2):
    provider, _ = r2
    key = provider.save(io.BytesIO(b"%PDF-1.4 return"), "storage/client_documents/C1/2025/F1/Other/return_v1_cd34.pdf")

    r = send_file(key, "Return 2025.pdf", inline=True)
    assert r.body == b"%PDF-1.4 return"
    assert r.media_type == "application/pdf"
    assert r.headers["content-disposition"].startswith("inline")

    with pytest.raises(AppError):
        send_file("storage/client_documents/C1/2025/F1/Other/gone.pdf", "Gone.pdf")


def test_payment_proofs_go_to_the_bucket(r2, monkeypatch):
    provider, fake = r2
    monkeypatch.setattr(payments, "storage_provider", provider)
    proof = UploadFile(file=io.BytesIO(b"%PDF-1.4 receipt"), filename="receipt.pdf")

    path, name, mime = payments._save_proof(proof, "CLI-1", "upi")

    assert path.startswith("private_storage/payment_proofs/CLI-1/upi_") and path.endswith(".pdf")
    assert ("covrenttax-documents", path) in fake.objects
    assert (name, mime) == ("receipt.pdf", "application/pdf")
    assert send_file(path, name).body == b"%PDF-1.4 receipt"


def test_storage_backend_setting_picks_the_provider(monkeypatch):
    monkeypatch.setattr(sp.settings, "STORAGE_BACKEND", "r2")
    assert isinstance(sp.build_storage_provider(), sp.R2StorageProvider)
    monkeypatch.setattr(sp.settings, "STORAGE_BACKEND", "local")
    assert isinstance(sp.build_storage_provider(), sp.LocalStorageProvider)


def _production(**overrides):
    from cryptography.fernet import Fernet

    values = dict(APP_ENV="production", DEBUG=False, SECRET_KEY="k" * 40, PUBLIC_BASE_URL="https://covrenttax.com",
                  ALLOWED_HOSTS="covrenttax.com", DATA_ENCRYPTION_KEYS=Fernet.generate_key().decode())
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_production_refuses_r2_without_its_keys():
    with pytest.raises(ValueError, match="R2_ENDPOINT"):
        _production(STORAGE_BACKEND="r2", R2_BUCKET="covrenttax-documents")
    with pytest.raises(ValueError, match="STORAGE_BACKEND must be local or r2"):
        _production(STORAGE_BACKEND="s3")
    ok = _production(STORAGE_BACKEND="r2", R2_ENDPOINT="https://acct.r2.cloudflarestorage.com",
                     R2_BUCKET="covrenttax-documents", R2_ACCESS_KEY_ID="id", R2_SECRET_ACCESS_KEY="secret")
    assert ok.STORAGE_BACKEND == "r2"
