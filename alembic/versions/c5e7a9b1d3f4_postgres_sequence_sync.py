"""PostgreSQL: move every id sequence past the rows already in its table

Some seed migrations insert rows with explicit ids (e.g. chat_query_topics). MySQL's AUTO_INCREMENT moves on by
itself; a PostgreSQL sequence does not, so the next row the app adds (an admin adding a chat topic or a tax year)
gets an id that is already taken: "duplicate key value violates unique constraint". This lines every sequence up
with MAX(id). Safe to run again; does nothing on MySQL or SQLite.

Revision ID: c5e7a9b1d3f4
Revises: b3d5f7a9c1e2
Create Date: 2026-11-02

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'c5e7a9b1d3f4'
down_revision = 'b3d5f7a9c1e2'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name != 'postgresql':
        return
    # Every column in this schema that owns a sequence (SERIAL / IDENTITY).
    owned = bind.execute(sa.text("""
        SELECT c.table_name, c.column_name, pg_get_serial_sequence(quote_ident(c.table_name), c.column_name) AS seq
        FROM information_schema.columns c
        WHERE c.table_schema = current_schema()
          AND pg_get_serial_sequence(quote_ident(c.table_name), c.column_name) IS NOT NULL
    """)).fetchall()
    for table, column, seq in owned:
        # setval(seq, max, true): the next value is max + 1. An empty table starts at 1 (setval(seq, 1, false)).
        bind.execute(sa.text(
            f'SELECT setval(:seq, COALESCE((SELECT MAX("{column}") FROM "{table}"), 1), '
            f'(SELECT MAX("{column}") FROM "{table}") IS NOT NULL)'), {"seq": seq})


def downgrade():
    pass  # nothing to undo: the sequences only moved forward
