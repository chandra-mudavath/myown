"""add_staff_role_column

Revision ID: e922b47ac98b
Revises: 3e0224cd65e9
Create Date: 2026-09-01 12:31:50.316129

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e922b47ac98b'
down_revision: Union[str, None] = '3e0224cd65e9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

staffrole_enum = sa.Enum('INITIATOR', 'PREPARER', 'REVIEWER', 'MANAGER', 'HR', 'ADMIN', name='staffrole')


def upgrade() -> None:
    # 1. Create the enum type first
    staffrole_enum.create(op.get_bind(), checkfirst=True)

    # 2. Add column as nullable so existing rows don't violate NOT NULL
    op.add_column('staff', sa.Column('role', staffrole_enum, nullable=True))

    # 3. Backfill all existing staff rows with default role INITIATOR
    op.execute("UPDATE staff SET role = 'INITIATOR' WHERE role IS NULL")

    # 4. Now enforce NOT NULL
    op.alter_column('staff', 'role', existing_type=staffrole_enum, nullable=False)


def downgrade() -> None:
    op.drop_column('staff', 'role')
    staffrole_enum.drop(op.get_bind(), checkfirst=True)
