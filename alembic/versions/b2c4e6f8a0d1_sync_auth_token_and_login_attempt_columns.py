"""sync_auth_token_and_login_attempt_columns

Revision ID: b2c4e6f8a0d1
Revises: 9f8a7b6c5d4e
Create Date: 2026-09-27

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'b2c4e6f8a0d1'
down_revision = '9f8a7b6c5d4e'
branch_labels = None
depends_on = None


def upgrade():
    # Renames (not drop/add) so existing rows are preserved.
    op.alter_column(
        'auth_email_verification_tokens', 'used_at',
        new_column_name='verified_at',
        existing_type=sa.DateTime(timezone=True), existing_nullable=True,
    )
    op.alter_column(
        'auth_login_attempts', 'was_successful',
        new_column_name='success',
        existing_type=sa.Boolean(), existing_nullable=False,
    )
    op.alter_column(
        'auth_login_attempts', 'failure_reason',
        type_=sa.String(length=200),
        existing_type=sa.String(length=100), existing_nullable=True,
    )


def downgrade():
    op.alter_column(
        'auth_login_attempts', 'failure_reason',
        type_=sa.String(length=100),
        existing_type=sa.String(length=200), existing_nullable=True,
    )
    op.alter_column(
        'auth_login_attempts', 'success',
        new_column_name='was_successful',
        existing_type=sa.Boolean(), existing_nullable=False,
    )
    op.alter_column(
        'auth_email_verification_tokens', 'verified_at',
        new_column_name='used_at',
        existing_type=sa.DateTime(timezone=True), existing_nullable=True,
    )
