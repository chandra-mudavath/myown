"""Razorpay Smart Collect: virtual_accounts (each client's own bank account number for INR bank transfers)

Revision ID: f3a5c7e9b1d4
Revises: e1f3a5c7b9d2
Create Date: 2026-11-10

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'f3a5c7e9b1d4'
down_revision = 'e1f3a5c7b9d2'
branch_labels = None
depends_on = None


def upgrade():
    # main.py's create_all may already have made it on startup.
    if 'virtual_accounts' in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        'virtual_accounts',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('gateway', sa.String(20), nullable=False),
        sa.Column('client_id', sa.String(36), sa.ForeignKey('clients.id', ondelete='CASCADE'), nullable=False),
        sa.Column('gateway_account_id', sa.String(100), nullable=False, unique=True),
        sa.Column('account_number', sa.String(40), nullable=False),
        sa.Column('ifsc', sa.String(20), nullable=False),
        sa.Column('bank_name', sa.String(120), nullable=True),
        sa.Column('account_name', sa.String(120), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint('gateway', 'client_id', name='uq_virtual_accounts_gateway_client'),
    )


def downgrade():
    op.drop_table('virtual_accounts')
