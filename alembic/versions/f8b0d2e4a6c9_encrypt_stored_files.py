"""Encrypt stored files saved before encryption at rest: client documents, chat attachments, payment proofs

No schema change. Each file named in filing_documents.file_path, chat_attachments.file_path and
payments.proof_path is rewritten on disk as FILE_MAGIC + Fernet token (see app/core/crypto.py).
Run from the project root (paths are relative to it), with DATA_ENCRYPTION_KEYS set in production.
Missing files are skipped. Downgrade decrypts them back.

Revision ID: f8b0d2e4a6c9
Revises: e6a8c0b2d4f7
Create Date: 2026-10-23

"""
import os
from pathlib import Path

from alembic import op
import sqlalchemy as sa

from app.core.crypto import decrypt_bytes, encrypt_bytes, is_encrypted_bytes

# revision identifiers, used by Alembic.
revision = 'f8b0d2e4a6c9'
down_revision = 'e6a8c0b2d4f7'
branch_labels = None
depends_on = None

FILE_COLUMNS = [('filing_documents', 'file_path'), ('chat_attachments', 'file_path'), ('payments', 'proof_path')]


def _paths(bind):
    tables = set(sa.inspect(bind).get_table_names())
    for table, column in FILE_COLUMNS:
        if table in tables:
            for (path,) in bind.execute(sa.text(f"SELECT {column} FROM {table} WHERE {column} IS NOT NULL")):
                yield path


def _rewrite(convert, wanted):
    for path in set(_paths(op.get_bind())):
        p = Path(path)
        if not p.is_file():
            continue
        blob = p.read_bytes()
        if is_encrypted_bytes(blob) == wanted:
            continue
        tmp = p.with_name(p.name + '.tmp')
        tmp.write_bytes(convert(blob))
        os.replace(tmp, p)  # a crash mid-way never leaves a half-written file


def upgrade():
    _rewrite(encrypt_bytes, wanted=True)


def downgrade():
    _rewrite(decrypt_bytes, wanted=False)
