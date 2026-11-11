"""readable numbers: id_sequences counters, document numbers, required case numbers

- Adds id_sequences, one counter per kind of readable number, starting after the highest
  number already in use (so numbers freed by deleted rows are never handed out again).
- Adds document_number to filing_documents and staff_documents and backfills it.
- Gives every case without a case_number one, then makes case_number required.

Revision ID: e5f7b9d1a3c4
Revises: d4e6a8c0f2b3
Create Date: 2026-10-01

"""
import re
from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'e5f7b9d1a3c4'
down_revision = 'd4e6a8c0f2b3'
branch_labels = None
depends_on = None


# name -> (prefix, width, table, column); mirrors SEQUENCES in app/services/numbering.py
SEQUENCES = {
    'ACCOUNT_CLIENT': ('CLT-', 8, 'auth_accounts', 'account_number'),
    'ACCOUNT_STAFF': ('STF-', 8, 'auth_accounts', 'account_number'),
    'ACCOUNT_ADMIN': ('ADM-', 8, 'auth_accounts', 'account_number'),
    'ACCOUNT_HR': ('HR-', 8, 'auth_accounts', 'account_number'),
    'CLIENT': ('CLI-', 8, 'clients', 'client_number'),
    'STAFF': ('STF-', 8, 'staff', 'staff_number'),
    'ADMIN': ('ADM-', 8, 'admins', 'admin_number'),
    'HR': ('HR-', 8, 'hr', 'hr_number'),
    'EMPLOYEE': ('EMP-', 6, 'staff_employment_details', 'employee_number'),
    'CASE': ('FLI_', 7, 'tax_filings', 'case_number'),
    'DOCUMENT': ('DOC-', 7, 'filing_documents', 'document_number'),
    'STAFF_DOCUMENT': ('SDOC-', 7, 'staff_documents', 'document_number'),
    'IRS_SUBMISSION': ('SUB-', 7, 'irs_submissions', 'submission_number'),
    'ACKNOWLEDGMENT': ('ACK-', 7, 'acknowledgments', 'ack_number'),
    'INVOICE': ('INV-', 7, 'invoices', 'invoice_number'),
    'PAYMENT': ('PAY-', 7, 'payments', 'payment_number'),
}


def _columns(insp, table):
    return {c['name'] for c in insp.get_columns(table)}


def _highest_in_use(bind, prefix, table, column):
    pattern = re.compile(re.escape(prefix) + r'(\d+)$')
    rows = bind.execute(sa.text(f'SELECT {column} FROM {table} WHERE {column} LIKE :p'), {'p': prefix + '%'})
    return max((int(m.group(1)) for (v,) in rows if v and (m := pattern.match(v))), default=0)


def _backfill(bind, table, column, prefix, width, order_by, start):
    """Number every row in `table` whose `column` is empty, oldest first. Returns the last value used."""
    rows = bind.execute(sa.text(
        f"SELECT id FROM {table} WHERE {column} IS NULL OR {column} = '' ORDER BY {order_by}, id"
    )).fetchall()
    value = start
    for (row_id,) in rows:
        value += 1
        bind.execute(sa.text(f'UPDATE {table} SET {column} = :n WHERE id = :id'),
                     {'n': f'{prefix}{value:0{width}d}', 'id': row_id})
    return value


def upgrade():
    bind = op.get_bind()
    insp = sa.inspect(bind)

    # app/main.py runs create_all() on startup, so id_sequences may already exist.
    if not insp.has_table('id_sequences'):
        op.create_table(
            'id_sequences',
            sa.Column('name', sa.String(length=40), primary_key=True),
            sa.Column('prefix', sa.String(length=10), nullable=False),
            sa.Column('width', sa.Integer(), nullable=False),
            sa.Column('current_value', sa.Integer(), nullable=False),
            sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        )
    elif 'last_value' in _columns(insp, 'id_sequences'):
        # An earlier attempt of this migration named the column last_value, which is a reserved
        # word in MySQL 8 (the LAST_VALUE() window function). Rename it.
        with op.batch_alter_table('id_sequences') as batch:
            batch.alter_column('last_value', new_column_name='current_value',
                               existing_type=sa.Integer(), existing_nullable=False)

    for table in ('filing_documents', 'staff_documents'):
        if 'document_number' not in _columns(insp, table):
            op.add_column(table, sa.Column('document_number', sa.String(length=20), nullable=True))

    # Work out each counter's starting point, numbering any rows that have no number yet.
    current_values = {}
    for name, (prefix, width, table, column) in SEQUENCES.items():
        current_values[name] = _highest_in_use(bind, prefix, table, column)
    current_values['CASE'] = _backfill(bind, 'tax_filings', 'case_number', 'FLI_', 7, 'created_at', current_values['CASE'])
    current_values['DOCUMENT'] = _backfill(bind, 'filing_documents', 'document_number', 'DOC-', 7, 'uploaded_at', current_values['DOCUMENT'])
    current_values['STAFF_DOCUMENT'] = _backfill(bind, 'staff_documents', 'document_number', 'SDOC-', 7, 'uploaded_at', current_values['STAFF_DOCUMENT'])

    now = datetime.now(timezone.utc)
    sequences = sa.table('id_sequences', sa.column('name'), sa.column('prefix'), sa.column('width'),
                         sa.column('current_value'), sa.column('updated_at'))
    existing = {r[0]: r[1] for r in bind.execute(sa.text('SELECT name, current_value FROM id_sequences'))}
    for name, (prefix, width, _table, _column) in SEQUENCES.items():
        if name in existing:
            # The app may already have created this counter; never move it backwards.
            if existing[name] < current_values[name]:
                bind.execute(sa.text('UPDATE id_sequences SET current_value = :v WHERE name = :n'),
                             {'v': current_values[name], 'n': name})
        else:
            op.bulk_insert(sequences, [dict(name=name, prefix=prefix, width=width,
                                            current_value=current_values[name], updated_at=now)])

    # Every row has a number now, so make the columns required (batch mode for SQLite).
    with op.batch_alter_table('tax_filings') as batch:
        batch.alter_column('case_number', existing_type=sa.String(length=30), nullable=False)
    for table in ('filing_documents', 'staff_documents'):
        with op.batch_alter_table(table) as batch:
            batch.alter_column('document_number', existing_type=sa.String(length=20), nullable=False)
        index_name = f'ix_{table}_document_number'
        if index_name not in {i['name'] for i in sa.inspect(bind).get_indexes(table)}:
            op.create_index(index_name, table, ['document_number'], unique=True)


def downgrade():
    for table in ('staff_documents', 'filing_documents'):
        op.drop_index(f'ix_{table}_document_number', table_name=table)
        with op.batch_alter_table(table) as batch:
            batch.drop_column('document_number')
    with op.batch_alter_table('tax_filings') as batch:
        batch.alter_column('case_number', existing_type=sa.String(length=30), nullable=True)
    op.drop_table('id_sequences')
