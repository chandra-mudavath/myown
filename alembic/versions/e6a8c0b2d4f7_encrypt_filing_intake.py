"""filing_intake.data: encrypt rows saved before encryption at rest (bank / FBAR numbers, SSN/TIN, e-file PINs)

No schema change: the column stays JSON and now holds {"_enc": "v1", "ct": "<Fernet token>"}.
Uses DATA_ENCRYPTION_KEYS (see app/core/crypto.py); set it BEFORE running this in production.
Downgrade decrypts the rows back to plain JSON.

Revision ID: e6a8c0b2d4f7
Revises: d5f7a9b1c3e6
Create Date: 2026-10-23

"""
import json

from alembic import op
import sqlalchemy as sa

from app.core.crypto import decrypt_json, encrypt_json, is_encrypted

# revision identifiers, used by Alembic.
revision = 'e6a8c0b2d4f7'
down_revision = 'd5f7a9b1c3e6'
branch_labels = None
depends_on = None

_rows = sa.table('filing_intake', sa.column('id', sa.String(36)), sa.column('data', sa.JSON()))


def _rewrite(convert, wanted):
    bind = op.get_bind()
    if 'filing_intake' not in sa.inspect(bind).get_table_names():
        return
    for row_id, data in bind.execute(sa.select(_rows.c.id, _rows.c.data)).all():
        if isinstance(data, str):  # some drivers hand JSON back as text
            data = json.loads(data)
        if data is not None and is_encrypted(data) != wanted:
            bind.execute(_rows.update().where(_rows.c.id == row_id).values(data=convert(data)))


def upgrade():
    _rewrite(encrypt_json, wanted=True)


def downgrade():
    _rewrite(decrypt_json, wanted=False)
