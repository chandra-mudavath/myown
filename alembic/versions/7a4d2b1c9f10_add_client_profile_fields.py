"""Add client profile fields.

Revision ID: 7a4d2b1c9f10
Revises: 5ef8482d63e9
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "7a4d2b1c9f10"
down_revision: Union[str, None] = "5ef8482d63e9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column("clients", "first_name", existing_type=sa.String(length=15), type_=sa.String(length=50), existing_nullable=False)
    op.alter_column("clients", "last_name", existing_type=sa.String(length=15), type_=sa.String(length=50), existing_nullable=True)
    op.alter_column("clients", "phone", existing_type=sa.String(length=20), type_=sa.String(length=25), existing_nullable=False)
    op.add_column("clients", sa.Column("date_of_birth", sa.Date(), nullable=True))
    op.add_column("clients", sa.Column("address_line_1", sa.String(length=120), nullable=True))
    op.add_column("clients", sa.Column("address_line_2", sa.String(length=120), nullable=True))
    op.add_column("clients", sa.Column("city", sa.String(length=80), nullable=True))
    op.add_column("clients", sa.Column("state_province", sa.String(length=80), nullable=True))
    op.add_column("clients", sa.Column("postal_code", sa.String(length=20), nullable=True))
    op.add_column("clients", sa.Column("country", sa.String(length=80), nullable=True))


def downgrade() -> None:
    op.drop_column("clients", "country")
    op.drop_column("clients", "postal_code")
    op.drop_column("clients", "state_province")
    op.drop_column("clients", "city")
    op.drop_column("clients", "address_line_2")
    op.drop_column("clients", "address_line_1")
    op.drop_column("clients", "date_of_birth")
    op.alter_column("clients", "last_name", existing_type=sa.String(length=50), type_=sa.String(length=15), existing_nullable=True)
    op.alter_column("clients", "phone", existing_type=sa.String(length=25), type_=sa.String(length=20), existing_nullable=False)
    op.alter_column("clients", "first_name", existing_type=sa.String(length=50), type_=sa.String(length=15), existing_nullable=False)