"""Stripe payments: fee / refunded amount / dispute on payments, and the payment_events webhook log

Revision ID: e1f3a5c7b9d2
Revises: d7f9b1c3e5a8
Create Date: 2026-11-05

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'e1f3a5c7b9d2'
down_revision = 'd7f9b1c3e5a8'
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    # main.py's create_all may already have made payment_events on startup; it never adds columns to an existing table.
    have = {c['name'] for c in inspector.get_columns('payments')}
    if 'fee_amount' not in have:
        op.add_column('payments', sa.Column('fee_amount', sa.Numeric(12, 2), nullable=True))
    if 'refunded_amount' not in have:
        op.add_column('payments', sa.Column('refunded_amount', sa.Numeric(12, 2), nullable=False, server_default='0'))
    if 'dispute_status' not in have:
        op.add_column('payments', sa.Column('dispute_status', sa.String(30), nullable=True))

    if 'payment_events' not in inspector.get_table_names():
        op.create_table(
            'payment_events',
            sa.Column('id', sa.String(36), primary_key=True),
            sa.Column('gateway', sa.String(20), nullable=False),
            sa.Column('event_id', sa.String(100), nullable=False),
            sa.Column('event_type', sa.String(100), nullable=False),
            sa.Column('payment_id', sa.String(36), sa.ForeignKey('payments.id', ondelete='SET NULL'), nullable=True),
            sa.Column('received_at', sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint('gateway', 'event_id', name='uq_payment_events_gateway_event'),
        )
        op.create_index('ix_payment_events_payment_id', 'payment_events', ['payment_id'])


def downgrade():
    op.drop_index('ix_payment_events_payment_id', table_name='payment_events')
    op.drop_table('payment_events')
    op.drop_column('payments', 'dispute_status')
    op.drop_column('payments', 'refunded_amount')
    op.drop_column('payments', 'fee_amount')
