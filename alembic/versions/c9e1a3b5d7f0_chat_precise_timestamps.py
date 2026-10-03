"""chat: microsecond timestamps on MySQL for message order and unread counts

MySQL DATETIME keeps whole seconds unless a precision is given, so two messages sent in
the same second could swap places and a message sent in the second a chat was read could
be missed as unread. PostgreSQL timestamps already keep microseconds; nothing changes there.

Revision ID: c9e1a3b5d7f0
Revises: b8d0f2a4c6e9
Create Date: 2026-10-02

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

# revision identifiers, used by Alembic.
revision = 'c9e1a3b5d7f0'
down_revision = 'b8d0f2a4c6e9'
branch_labels = None
depends_on = None


COLUMNS = [  # (table, column, nullable)
    ('chat_messages', 'created_at', False),
    ('chat_participants', 'last_read_at', True),
    ('chat_threads', 'last_message_at', True),
]


def _alter(precision):
    if op.get_bind().dialect.name != 'mysql':
        return
    for table, column, nullable in COLUMNS:
        op.alter_column(table, column, type_=mysql.DATETIME(fsp=precision) if precision else mysql.DATETIME(),
                        existing_type=sa.DateTime(), existing_nullable=nullable)


def upgrade():
    _alter(6)


def downgrade():
    _alter(0)
