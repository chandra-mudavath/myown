"""Security fixes: stored file types, upload checks, security headers, production settings, rate limits."""
from __future__ import annotations

import io
import re

import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.datastructures import Headers, UploadFile

from app.core.config import Settings
from app.core.errors import AppError
from app.core.files import mime_for, send_file
from app.main import app
from app.platform.models.auth import AuthAccount, AuthLoginAttempt
from app.platform.services import auth_service, storage_service

from tests.test_auth_email import PASSWORD, _account, _token, env  # noqa: F401  (fixture)


def _upload(name: str, data: bytes, content_type: str = "text/html") -> UploadFile:
    return UploadFile(io.BytesIO(data), filename=name, headers=Headers({"content-type": content_type}))


# ── Stored files ────────────────────────────────────────────────────────────

def test_file_type_comes_from_the_extension_not_the_browser(tmp_path):
    path = tmp_path / "notes_v1_ab.txt"
    path.write_text("<script>alert(1)</script>")
    r = send_file(str(path), "notes.txt", media_type="text/html", inline=True)
    assert r.media_type.startswith("text/plain")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert mime_for("x.html") == "application/octet-stream"


def test_only_safe_types_open_inline(tmp_path):
    path = tmp_path / "sheet.xlsx"
    path.write_bytes(b"PK")
    assert send_file(str(path), "sheet.xlsx", inline=True).headers["content-disposition"].startswith("attachment")
    pdf = tmp_path / "w2.pdf"
    pdf.write_bytes(b"%PDF")
    assert send_file(str(pdf), "w2.pdf", inline=True).headers["content-disposition"].startswith("inline")


def test_document_upload_checks_extension_and_size(tmp_path, monkeypatch):
    monkeypatch.setattr(storage_service.settings, "CLIENT_DOCUMENTS_DIR", str(tmp_path))
    monkeypatch.setattr(storage_service.settings, "MAX_UPLOAD_SIZE_MB", 1)
    with pytest.raises(HTTPException):
        storage_service.save_document_file(_upload("evil.html", b"<b>"), "C1", 2025, "F1", "W2")
    with pytest.raises(AppError) as big:
        storage_service.save_document_file(_upload("big.pdf", b"0" * (1024 * 1024 + 1)), "C1", 2025, "F1", "W2")
    assert big.value.status_code == 413
    _, _, mime, size = storage_service.save_document_file(_upload("notes.txt", b"hi"), "C1", 2025, "F1", "W2")
    assert mime.startswith("text/plain") and size == 2


def test_avatar_must_really_be_an_image(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(HTTPException):
        storage_service.save_profile_picture(_upload("me.png", b"<html>not an image</html>", "image/png"), "u1", "client")
    url = storage_service.save_profile_picture(_upload("me.jpeg", b"\x89PNG\r\n\x1a\n...", "image/jpeg"), "u1", "client")
    assert url.endswith(".png")  # the real type wins over the name


# ── Headers and production settings ─────────────────────────────────────────

def test_every_response_has_security_headers():
    r = TestClient(app).get("/auth/login")
    assert r.headers["x-frame-options"] == "SAMEORIGIN"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "frame-ancestors 'self'" in r.headers["content-security-policy"]
    assert r.headers["referrer-policy"] == "strict-origin-when-cross-origin"


def test_production_refuses_development_defaults():
    with pytest.raises(ValueError, match="DEBUG.*SECRET_KEY.*PUBLIC_BASE_URL"):
        Settings(_env_file=None, APP_ENV="production", DEBUG=True)
    with pytest.raises(ValueError, match="DATA_ENCRYPTION_KEYS"):
        Settings(_env_file=None, APP_ENV="production", DEBUG=False, SECRET_KEY="x" * 64,
                 PUBLIC_BASE_URL="https://covrenttax.com", ALLOWED_HOSTS="covrenttax.com", DATA_ENCRYPTION_KEYS="not-a-key")
    ok = Settings(_env_file=None, APP_ENV="production", DEBUG=False, SECRET_KEY="x" * 64,
                  PUBLIC_BASE_URL="https://covrenttax.com", ALLOWED_HOSTS="covrenttax.com",
                  DATA_ENCRYPTION_KEYS=Fernet.generate_key().decode())
    assert ok.is_production and ok.allowed_hosts == ["covrenttax.com"]


# ── Password reset and rate limits ──────────────────────────────────────────

def test_reset_password_needs_a_strong_password_and_clears_the_lock(env):
    client = TestClient(app)
    client.post("/auth/forgot-password", data={"email": "client@x.test"})
    token = _token(env["sent"][-1][3]["link"])
    db = env["Session"]()
    acc = db.query(AuthAccount).filter(AuthAccount.email == "client@x.test").one()
    acc.failed_login_count = 5
    db.commit()
    db.close()

    r = client.post("/auth/reset-password", data={"token": token, "new_password": "123", "confirm_password": "123"})
    assert "Password must contain" in r.text
    client.post("/auth/reset-password", data={"token": token, "new_password": "N3w!passw0rd", "confirm_password": "N3w!passw0rd"})
    assert _account(env, "client@x.test").failed_login_count == 0


def test_reset_emails_are_limited_per_account(env):
    client = TestClient(app)
    for _ in range(5):
        r = client.post("/auth/forgot-password", data={"email": "client@x.test"})
        assert "If an account exists" in r.text  # same answer every time
    assert len(env["sent"]) == auth_service.PASSWORD_RESET_EMAILS_PER_HOUR


def test_many_wrong_sign_ins_from_one_ip_are_blocked(env):
    db = env["Session"]()
    db.add_all([AuthLoginAttempt(email=f"u{i}@x.test", ip_address="testclient", success=False)
                for i in range(auth_service.IP_FAILED_LOGIN_LIMIT)])
    db.commit()
    db.close()
    r = TestClient(app).post("/auth/login", data={"username": "client@x.test", "password": PASSWORD}, follow_redirects=False)
    assert r.status_code == 429 and re.search(r"Too many sign-in attempts", r.text)
