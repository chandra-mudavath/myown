"""add_profile_picture_to_models

Revision ID: 9f8a7b6c5d4e
Revises: 1ca5f38d596b
Create Date: 2026-09-10

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '9f8a7b6c5d4e'
down_revision = '1ca5f38d596b'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('clients', sa.Column('profile_picture', sa.String(length=255), nullable=True))
    op.add_column('staff', sa.Column('profile_picture', sa.String(length=255), nullable=True))
    op.add_column('admins', sa.Column('profile_picture', sa.String(length=255), nullable=True))


def downgrade():
    op.drop_column('admins', 'profile_picture')
    op.drop_column('staff', 'profile_picture')
    op.drop_column('clients', 'profile_picture')
