"""auth_accounts.must_change_password: a temporary password from an admin must be changed at first sign-in

Revision ID: a9c1e3f5b7d0
Revises: f8b0d2e4a6c9
Create Date: 2026-10-23

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'a9c1e3f5b7d0'
down_revision = 'f8b0d2e4a6c9'
branch_labels = None
depends_on = None


def upgrade():
    # main.py's create_all may already have added it on a fresh database.
    if 'must_change_password' in {c['name'] for c in sa.inspect(op.get_bind()).get_columns('auth_accounts')}:
        return
    op.add_column('auth_accounts', sa.Column('must_change_password', sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade():
    op.drop_column('auth_accounts', 'must_change_password')
