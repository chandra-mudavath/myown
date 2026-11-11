"""email_change_requests: a client's new sign-in email waits for an admin's approval

Revision ID: d7f9b1c3e5a8
Revises: c5e7a9b1d3f4
Create Date: 2026-11-03

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'd7f9b1c3e5a8'
down_revision = 'c5e7a9b1d3f4'
branch_labels = None
depends_on = None


def upgrade():
    # main.py's create_all may already have made it on a fresh database.
    if 'email_change_requests' in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        'email_change_requests',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('account_id', sa.String(36), sa.ForeignKey('auth_accounts.id', ondelete='CASCADE'), nullable=False),
        sa.Column('old_email', sa.String(254), nullable=False),
        sa.Column('new_email', sa.String(254), nullable=False),
        sa.Column('status', sa.String(20), nullable=False, server_default='pending'),
        sa.Column('reason', sa.String(500), nullable=True),
        sa.Column('decided_by_account_id', sa.String(36), sa.ForeignKey('auth_accounts.id', ondelete='SET NULL'), nullable=True),
        sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_email_change_requests_account_id', 'email_change_requests', ['account_id'])
    op.create_index('ix_email_change_requests_status_created', 'email_change_requests', ['status', 'created_at'])


def downgrade():
    op.drop_index('ix_email_change_requests_status_created', table_name='email_change_requests')
    op.drop_index('ix_email_change_requests_account_id', table_name='email_change_requests')
    op.drop_table('email_change_requests')
