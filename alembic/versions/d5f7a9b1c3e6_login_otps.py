"""auth_login_otps: emailed sign-in / sign-up codes for staff and admin

Revision ID: d5f7a9b1c3e6
Revises: c4e6a8b0d2f5
Create Date: 2026-10-22

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'd5f7a9b1c3e6'
down_revision = 'c4e6a8b0d2f5'
branch_labels = None
depends_on = None


def upgrade():
    # main.py's create_all may already have made the table on a fresh database.
    if 'auth_login_otps' in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        'auth_login_otps',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('account_id', sa.String(36), sa.ForeignKey('auth_accounts.id', ondelete='CASCADE'), nullable=False),
        sa.Column('challenge_hash', sa.String(64), nullable=False, unique=True),
        sa.Column('code_hash', sa.String(64), nullable=False),
        sa.Column('purpose', sa.String(10), nullable=False),
        sa.Column('attempts', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('sends', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_sent_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_auth_login_otps_account_id', 'auth_login_otps', ['account_id'])


def downgrade():
    op.drop_index('ix_auth_login_otps_account_id', table_name='auth_login_otps')
    op.drop_table('auth_login_otps')
