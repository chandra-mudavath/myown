"""Add staff and admin models

Revision ID: 5ef8482d63e9
Revises: d8c920b8f239
Create Date: 2026-08-18 03:00:03.648206

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '5ef8482d63e9'
down_revision: Union[str, None] = 'd8c920b8f239'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Drop legacy users table if it exists
    op.drop_index('ix_users_email', table_name='users')
    op.drop_index('ix_users_id', table_name='users')
    op.drop_table('users')

    # Create auth_accounts
    op.create_table(
        'auth_accounts',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('account_number', sa.String(length=20), nullable=False),
        sa.Column('email', sa.String(length=254), nullable=False),
        sa.Column('password_hash', sa.String(length=512), nullable=False),
        sa.Column('account_type', sa.Enum('CLIENT', 'STAFF', 'ADMIN', name='accounttype'), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('1')),
        sa.Column('is_verified', sa.Boolean(), nullable=False, server_default=sa.text('0')),
        sa.Column('email_verified_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_login_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('failed_login_count', sa.Integer(), nullable=False, server_default=sa.text('0')),
        sa.Column('locked_until', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_auth_accounts_account_number'), 'auth_accounts', ['account_number'], unique=True)
    op.create_index(op.f('ix_auth_accounts_email'), 'auth_accounts', ['email'], unique=True)

    # Create clients
    op.create_table(
        'clients',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('client_number', sa.String(length=20), nullable=False),
        sa.Column('account_id', sa.String(length=36), nullable=False),
        sa.Column('first_name', sa.String(length=15), nullable=False),
        sa.Column('last_name', sa.String(length=15), nullable=True),
        sa.Column('phone', sa.String(length=20), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['account_id'], ['auth_accounts.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('account_id')
    )
    op.create_index(op.f('ix_clients_client_number'), 'clients', ['client_number'], unique=True)

    # Create staff
    op.create_table(
        'staff',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('staff_number', sa.String(length=20), nullable=False),
        sa.Column('account_id', sa.String(length=36), nullable=False),
        sa.Column('first_name', sa.String(length=15), nullable=False),
        sa.Column('last_name', sa.String(length=15), nullable=True),
        sa.Column('phone', sa.String(length=20), nullable=True),
        sa.Column('job_title', sa.String(length=50), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['account_id'], ['auth_accounts.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('account_id')
    )
    op.create_index(op.f('ix_staff_staff_number'), 'staff', ['staff_number'], unique=True)

    # Create admins
    op.create_table(
        'admins',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('admin_number', sa.String(length=20), nullable=False),
        sa.Column('account_id', sa.String(length=36), nullable=False),
        sa.Column('first_name', sa.String(length=15), nullable=False),
        sa.Column('last_name', sa.String(length=15), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['account_id'], ['auth_accounts.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('account_id')
    )
    op.create_index(op.f('ix_admins_admin_number'), 'admins', ['admin_number'], unique=True)

    # Create auth_sessions
    op.create_table(
        'auth_sessions',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('account_id', sa.String(length=36), nullable=False),
        sa.Column('session_token_hash', sa.String(length=256), nullable=False),
        sa.Column('ip_address', sa.String(length=45), nullable=True),
        sa.Column('user_agent', sa.Text(), nullable=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_activity_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['account_id'], ['auth_accounts.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('session_token_hash')
    )
    op.create_index(op.f('ix_auth_sessions_account_id'), 'auth_sessions', ['account_id'], unique=False)

    # Create auth_refresh_tokens
    op.create_table(
        'auth_refresh_tokens',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('account_id', sa.String(length=36), nullable=False),
        sa.Column('token_hash', sa.String(length=256), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['account_id'], ['auth_accounts.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('token_hash')
    )
    op.create_index(op.f('ix_auth_refresh_tokens_account_id'), 'auth_refresh_tokens', ['account_id'], unique=False)

    # Create auth_password_reset_tokens
    op.create_table(
        'auth_password_reset_tokens',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('account_id', sa.String(length=36), nullable=False),
        sa.Column('token_hash', sa.String(length=256), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['account_id'], ['auth_accounts.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('token_hash')
    )
    op.create_index(op.f('ix_auth_password_reset_tokens_account_id'), 'auth_password_reset_tokens', ['account_id'], unique=False)

    # Create auth_email_verification_tokens
    op.create_table(
        'auth_email_verification_tokens',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('account_id', sa.String(length=36), nullable=False),
        sa.Column('token_hash', sa.String(length=256), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['account_id'], ['auth_accounts.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('token_hash')
    )
    op.create_index(op.f('ix_auth_email_verification_tokens_account_id'), 'auth_email_verification_tokens', ['account_id'], unique=False)

    # Create auth_login_attempts
    op.create_table(
        'auth_login_attempts',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('account_id', sa.String(length=36), nullable=True),
        sa.Column('email', sa.String(length=254), nullable=False),
        sa.Column('ip_address', sa.String(length=45), nullable=True),
        sa.Column('user_agent', sa.Text(), nullable=True),
        sa.Column('was_successful', sa.Boolean(), nullable=False),
        sa.Column('failure_reason', sa.String(length=100), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['account_id'], ['auth_accounts.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_auth_login_attempts_account_id'), 'auth_login_attempts', ['account_id'], unique=False)
    op.create_index(op.f('ix_auth_login_attempts_email'), 'auth_login_attempts', ['email'], unique=False)


def downgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table('users',
    sa.Column('id', sa.INTEGER(), autoincrement=True, nullable=False),
    sa.Column('email', sa.VARCHAR(length=254), autoincrement=False, nullable=False),
    sa.Column('password_hash', sa.VARCHAR(length=512), autoincrement=False, nullable=False),
    sa.Column('full_name', sa.VARCHAR(length=200), autoincrement=False, nullable=False),
    sa.Column('role', sa.Enum('admin', 'staff', 'client', name='userrole'), autoincrement=False, nullable=False),
    sa.Column('is_active', sa.BOOLEAN(), autoincrement=False, nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), autoincrement=False, nullable=False),
    sa.Column('last_login', sa.DateTime(timezone=True), autoincrement=False, nullable=True),
    sa.PrimaryKeyConstraint('id', name='users_pkey')
    )
    op.create_index('ix_users_id', 'users', ['id'], unique=False)
    op.create_index('ix_users_email', 'users', ['email'], unique=True)
    # ### end Alembic commands ###
