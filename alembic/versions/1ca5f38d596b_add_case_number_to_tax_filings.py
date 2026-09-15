"""add_case_number_to_tax_filings

Revision ID: 1ca5f38d596b
Revises: 83f96ca208c6
Create Date: 2026-09-10 05:32:52.915787

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1ca5f38d596b'
down_revision: Union[str, None] = '83f96ca208c6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Add column case_number as nullable
    op.add_column('tax_filings', sa.Column('case_number', sa.String(length=30), nullable=True))

    # 2. Backfill existing filings ordered by created_at date
    connection = op.get_bind()
    results = connection.execute(
        sa.text("SELECT id FROM tax_filings ORDER BY created_at ASC, id ASC")
    ).fetchall()

    for idx, row in enumerate(results, start=1):
        case_num = f"FLI_{idx:07d}"
        connection.execute(
            sa.text("UPDATE tax_filings SET case_number = :case_num WHERE id = :filing_id"),
            {"case_num": case_num, "filing_id": row[0]}
        )

    # 3. Create index and unique constraint
    op.create_index(op.f('ix_tax_filings_case_number'), 'tax_filings', ['case_number'], unique=True)


def downgrade() -> None:
    op.drop_index(op.f('ix_tax_filings_case_number'), table_name='tax_filings')
    op.drop_column('tax_filings', 'case_number')
