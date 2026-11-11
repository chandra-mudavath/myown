"""Encryption at rest: filing_intake.data (bank / FBAR numbers, SSN/TIN, e-file PINs) never reaches the database in plain text."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from cryptography.fernet import Fernet
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.db_models  # noqa: F401
from app.core import crypto
from app.core.config import settings
from app.core.database import Base
from app.platform.models.filing_intake import FilingIntake

SECRET = {"routing_number": "021000021", "account_number": "000123456789", "taxpayer_pin": "12345"}


@pytest.fixture()
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[FilingIntake.__table__])  # no foreign keys enforced: rows stand alone
    Session = sessionmaker(bind=engine)
    yield engine, Session
    crypto._fernet.cache_clear()


@pytest.fixture()
def key(monkeypatch):
    k = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "DATA_ENCRYPTION_KEYS", k)
    crypto._fernet.cache_clear()
    yield k
    crypto._fernet.cache_clear()


def _raw(engine) -> str:
    with engine.connect() as conn:
        return conn.execute(text("SELECT data FROM filing_intake")).scalar_one()


def test_database_holds_only_ciphertext(db, key):
    engine, Session = db
    s = Session()
    s.add(FilingIntake(filing_id="F1", section="bank", data=SECRET))
    s.commit()
    raw = _raw(engine)
    assert "000123456789" not in raw and "12345" not in json.loads(raw)["ct"] and json.loads(raw)["_enc"] == "v1"
    s.expire_all()
    assert s.query(FilingIntake).one().data == SECRET  # plain again in Python
    s.close()


def test_rows_saved_before_encryption_still_read_and_get_encrypted_on_save(db, key):
    engine, Session = db
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO filing_intake (id, filing_id, section, data, created_at, updated_at) "
                          "VALUES ('r1', 'F1', 'bank', :d, '2026-01-01', '2026-01-01')"), {"d": json.dumps(SECRET)})
    s = Session()
    row = s.query(FilingIntake).one()
    assert row.data == SECRET
    row.data = {**row.data, "holder_name": "Mike"}
    s.commit()
    assert "000123456789" not in _raw(engine)
    s.close()


def test_key_rotation_keeps_old_data_readable(db, monkeypatch):
    engine, Session = db
    old, new = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "DATA_ENCRYPTION_KEYS", old)
    crypto._fernet.cache_clear()
    s = Session()
    s.add(FilingIntake(filing_id="F1", section="efile_auth", data=SECRET))
    s.commit()
    s.close()
    monkeypatch.setattr(settings, "DATA_ENCRYPTION_KEYS", f"{new},{old}")
    crypto._fernet.cache_clear()
    assert Session().query(FilingIntake).one().data == SECRET


def test_a_wrong_key_fails_loudly_instead_of_returning_garbage(db, monkeypatch):
    engine, Session = db
    monkeypatch.setattr(settings, "APP_ENV", "production")  # no development fallback key
    monkeypatch.setattr(settings, "DATA_ENCRYPTION_KEYS", Fernet.generate_key().decode())
    crypto._fernet.cache_clear()
    s = Session()
    s.add(FilingIntake(filing_id="F1", section="bank", data=SECRET))
    s.commit()
    s.close()
    monkeypatch.setattr(settings, "DATA_ENCRYPTION_KEYS", Fernet.generate_key().decode())
    crypto._fernet.cache_clear()
    with pytest.raises(RuntimeError, match="can't be decrypted"):
        Session().query(FilingIntake).one()


def test_migration_encrypts_existing_rows_and_downgrade_reverses_it(db, key):
    engine, _ = db
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO filing_intake (id, filing_id, section, data, created_at, updated_at) "
                          "VALUES ('r1', 'F1', 'bank', :d, '2026-01-01', '2026-01-01')"), {"d": json.dumps(SECRET)})
    path = next(Path("alembic/versions").glob("e6a8c0b2d4f7_*.py"))
    spec = importlib.util.spec_from_file_location("encrypt_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    def run(step):
        with engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
            step()

    run(migration.upgrade)
    assert "000123456789" not in _raw(engine)
    run(migration.upgrade)  # running twice doesn't encrypt twice
    assert crypto.decrypt_json(json.loads(_raw(engine))) == SECRET
    run(migration.downgrade)
    assert json.loads(_raw(engine)) == SECRET


# ── Stored files ────────────────────────────────────────────────────────────

def test_uploaded_documents_are_encrypted_on_disk_and_sent_decrypted(tmp_path, monkeypatch, key):
    from app.core.files import send_file
    from app.platform.services import storage_service
    from tests.test_security import _upload

    monkeypatch.setattr(storage_service.settings, "CLIENT_DOCUMENTS_DIR", str(tmp_path))
    pdf = b"%PDF-1.7 W-2 for SSN 123-45-6789"
    _, _, _, size = storage_service.save_document_file(_upload("w2.pdf", pdf, "application/pdf"), "C1", 2025, "F1", "W2")
    stored = next(tmp_path.rglob("*.pdf"))
    on_disk = stored.read_bytes()
    assert on_disk.startswith(crypto.FILE_MAGIC) and b"123-45-6789" not in on_disk and size == len(pdf)
    r = send_file(str(stored), "w2.pdf", inline=True)
    assert r.body == pdf and r.headers["content-disposition"] == 'inline; filename="w2.pdf"'


def test_file_migration_encrypts_existing_files(db, key, tmp_path, monkeypatch):
    engine, _ = db
    monkeypatch.chdir(tmp_path)
    Path("docs").mkdir()
    Path("docs/old.pdf").write_bytes(b"%PDF old plain file")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE filing_documents (id TEXT, file_path TEXT)"))
        conn.execute(text("INSERT INTO filing_documents VALUES ('d1', 'docs/old.pdf'), ('d2', 'docs/missing.pdf')"))
    path = next((Path(__file__).parent.parent / "alembic/versions").glob("f8b0d2e4a6c9_*.py"))
    spec = importlib.util.spec_from_file_location("file_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    def run(step):
        with engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
            step()

    run(migration.upgrade)
    run(migration.upgrade)  # idempotent, and the missing file is skipped
    blob = Path("docs/old.pdf").read_bytes()
    assert crypto.is_encrypted_bytes(blob) and crypto.decrypt_bytes(blob) == b"%PDF old plain file"
    run(migration.downgrade)
    assert Path("docs/old.pdf").read_bytes() == b"%PDF old plain file"
