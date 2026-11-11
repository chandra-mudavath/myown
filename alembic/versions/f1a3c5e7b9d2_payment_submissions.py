"""payments: client submissions (manual UPI / bank transfer) and gateway fields

Revision ID: f1a3c5e7b9d2
Revises: e8f0a2b4c6d9
Create Date: 2026-10-06

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'f1a3c5e7b9d2'
down_revision = 'e8f0a2b4c6d9'
branch_labels = None
depends_on = None

COLUMNS = [
    sa.Column('gateway', sa.String(20), nullable=False, server_default='manual'),
    sa.Column('gateway_payment_id', sa.String(100), nullable=True),
    sa.Column('submitted_by_account_id', sa.String(36), nullable=True),
    sa.Column('original_amount', sa.Numeric(14, 2), nullable=True),
    sa.Column('original_currency', sa.String(3), nullable=True),
    sa.Column('proof_path', sa.String(500), nullable=True),
    sa.Column('proof_filename', sa.String(255), nullable=True),
    sa.Column('proof_mime', sa.String(100), nullable=True),
    sa.Column('verified_by_account_id', sa.String(36), nullable=True),
    sa.Column('verified_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('failure_reason', sa.String(500), nullable=True),
]


def upgrade():
    # main.py's create_all may already have added these on a fresh database, so only add what's missing.
    existing = {c['name'] for c in sa.inspect(op.get_bind()).get_columns('payments')}
    for col in COLUMNS:
        if col.name not in existing:
            op.add_column('payments', col.copy())
    if 'gateway_payment_id' not in existing:
        op.create_unique_constraint('uq_payments_gateway_payment_id', 'payments', ['gateway_payment_id'])
    if 'submitted_by_account_id' not in existing:
        op.create_foreign_key('fk_pay_submitted_by', 'payments', 'auth_accounts', ['submitted_by_account_id'], ['id'], ondelete='SET NULL')
    if 'verified_by_account_id' not in existing:
        op.create_foreign_key('fk_pay_verified_by', 'payments', 'auth_accounts', ['verified_by_account_id'], ['id'], ondelete='SET NULL')


def downgrade():
    op.drop_constraint('fk_pay_verified_by', 'payments', type_='foreignkey')
    op.drop_constraint('fk_pay_submitted_by', 'payments', type_='foreignkey')
    op.drop_constraint('uq_payments_gateway_payment_id', 'payments', type_='unique')
    for col in reversed(COLUMNS):
        op.drop_column('payments', col.name)
