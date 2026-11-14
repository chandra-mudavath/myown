"""Taxpayer details on the client: middle name (or none), gender, occupation and SSN (encrypted JSON)

Revision ID: d1f3a5c7e9b2
Revises: c8e0a2b4d6f9
Create Date: 2026-11-13

"""
from alembic import op
import sqlalchemy as sa

revision = 'd1f3a5c7e9b2'
down_revision = 'c8e0a2b4d6f9'
branch_labels = None
depends_on = None


def _new_columns():
    return [
        sa.Column('middle_name', sa.String(50), nullable=True),
        sa.Column('no_middle_name', sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column('gender', sa.String(10), nullable=True),
        sa.Column('occupation', sa.String(80), nullable=True),
        sa.Column('ssn', sa.JSON(), nullable=True),
    ]


def upgrade():
    existing = {c['name'] for c in sa.inspect(op.get_bind()).get_columns('clients')}
    for column in _new_columns():
        if column.name not in existing:  # main.py's create_all may have added them already
            op.add_column('clients', column)


def downgrade():
    for column in reversed(_new_columns()):
        op.drop_column('clients', column.name)
