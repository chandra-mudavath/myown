"""Payroll details on the employment record: PAN, UAN, PF number and bank (one encrypted JSON column)

Revision ID: c8e0a2b4d6f9
Revises: b6d8f0a2c4e7
Create Date: 2026-11-11

"""
from alembic import op
import sqlalchemy as sa

revision = 'c8e0a2b4d6f9'
down_revision = 'b6d8f0a2c4e7'
branch_labels = None
depends_on = None


def upgrade():
    columns = {c['name'] for c in sa.inspect(op.get_bind()).get_columns('staff_employment_details')}
    if 'payroll_ids' not in columns:  # main.py's create_all may have made the table with it already
        op.add_column('staff_employment_details', sa.Column('payroll_ids', sa.JSON(), nullable=True))


def downgrade():
    op.drop_column('staff_employment_details', 'payroll_ids')
