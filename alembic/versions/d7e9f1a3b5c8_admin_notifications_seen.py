"""admin notification read marker

Revision ID: d7e9f1a3b5c8
Revises: c9e1a3b5d7f0
Create Date: 2026-10-03

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'd7e9f1a3b5c8'
down_revision = 'c9e1a3b5d7f0'
branch_labels = None
depends_on = None


def upgrade():
    # Same marker staff already have: activity newer than this shows as "New" on /admin/notifications.
    insp = sa.inspect(op.get_bind())
    if 'notifications_seen_at' not in {c['name'] for c in insp.get_columns('admins')}:
        op.add_column('admins', sa.Column('notifications_seen_at', sa.DateTime(timezone=True), nullable=True))


def downgrade():
    op.drop_column('admins', 'notifications_seen_at')
