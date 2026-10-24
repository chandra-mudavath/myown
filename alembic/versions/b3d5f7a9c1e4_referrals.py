"""referrals: referred colleagues, follow-up calls, and clients.referred_by_client_id

Revision ID: b3d5f7a9c1e4
Revises: a2c4e6f8b0d3
Create Date: 2026-10-21

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'b3d5f7a9c1e4'
down_revision = 'a2c4e6f8b0d3'
branch_labels = None
depends_on = None


def upgrade():
    # main.py's create_all may already have made the new tables on a fresh database.
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())

    if 'referral_leads' not in tables:
        op.create_table(
            'referral_leads',
            sa.Column('id', sa.String(36), primary_key=True),
            sa.Column('referrer_client_id', sa.String(36), sa.ForeignKey('clients.id', ondelete='SET NULL'), nullable=True),
            sa.Column('name', sa.String(120), nullable=False),
            sa.Column('phone', sa.String(30), nullable=True),
            sa.Column('email', sa.String(255), nullable=True),
            sa.Column('relationship', sa.String(60), nullable=True),
            sa.Column('note', sa.Text(), nullable=True),
            sa.Column('source', sa.String(10), nullable=False, server_default='form'),
            sa.Column('status', sa.String(20), nullable=False, server_default='new'),
            sa.Column('assigned_staff_id', sa.String(36), sa.ForeignKey('staff.id', ondelete='SET NULL'), nullable=True),
            sa.Column('next_follow_up', sa.Date(), nullable=True),
            sa.Column('last_contacted_at', sa.DateTime(timezone=True), nullable=True),
            sa.Column('signed_up_client_id', sa.String(36), sa.ForeignKey('clients.id', ondelete='SET NULL'), nullable=True),
            sa.Column('created_by_account_id', sa.String(36), nullable=True),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
            sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index('ix_referral_leads_status', 'referral_leads', ['status', 'created_at'])
        op.create_index('ix_referral_leads_staff', 'referral_leads', ['assigned_staff_id', 'status'])
        op.create_index('ix_referral_leads_referrer', 'referral_leads', ['referrer_client_id'])

    if 'referral_followups' not in tables:
        op.create_table(
            'referral_followups',
            sa.Column('id', sa.String(36), primary_key=True),
            sa.Column('lead_id', sa.String(36), sa.ForeignKey('referral_leads.id', ondelete='CASCADE'), nullable=False),
            sa.Column('kind', sa.String(10), nullable=False, server_default='call'),
            sa.Column('outcome', sa.String(30), nullable=True),
            sa.Column('note', sa.Text(), nullable=True),
            sa.Column('author_account_id', sa.String(36), nullable=True),
            sa.Column('author_name', sa.String(80), nullable=False),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index('ix_referral_followups_lead', 'referral_followups', ['lead_id', 'created_at'])

    client_cols = {c['name'] for c in inspector.get_columns('clients')}
    if 'referred_by_client_id' not in client_cols:
        op.add_column('clients', sa.Column('referred_by_client_id', sa.String(36), nullable=True))


def downgrade():
    op.drop_column('clients', 'referred_by_client_id')
    op.drop_table('referral_followups')
    op.drop_table('referral_leads')
