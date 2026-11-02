"""lookups hold the real values: workflow stages, tax types, document categories

The first lookup seed (d4e6a8c0f2b3) used placeholder codes from the planning doc. The
app actually stores the 20 staff workflow stages and lowercase filing types used by the
forms, so this replaces the placeholder rows with those, and adds document_categories for
the category lists that were hardcoded in four places.

Placeholder rows are only removed when nothing references them. Rows that already exist
(e.g. an admin added a stage) are left alone.

Revision ID: f6a8c0e2b4d7
Revises: e5f7b9d1a3c4
Create Date: 2026-10-01

"""
from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'f6a8c0e2b4d7'
down_revision = 'e5f7b9d1a3c4'
branch_labels = None
depends_on = None


# (code, name, client_status). Codes are the values tax_filings.status already stores; the
# order is the staff pipeline order that app/api/staff.py hardcoded as STAFF_STAGES.
STAGES = [
    ('pending_review', 'Registered', 'Submitted'),
    ('info_pending', 'Info Pending', 'In Progress'),
    ('docs_pending', 'Docs Pending', 'In Progress'),
    ('docs_received', 'Docs Received', 'In Progress'),
    ('in_progress', 'In Progress', 'In Progress'),
    ('drip', 'DRIP', 'In Progress'),
    ('preparation', 'Preparation', 'In Progress'),
    ('review', 'Review', 'In Progress'),
    ('payment_pending', 'Payment Pending', 'In Progress'),
    ('draft_uploading', 'Draft Uploading', 'In Progress'),
    ('client_review', 'Client Review', 'In Progress'),
    ('rev_doc_rej', 'Rev Doc Rej', 'In Progress'),
    ('form_8879', 'Form 8879', 'In Progress'),
    ('e_filing', 'E-Filing', 'In Progress'),
    ('paper_filing', 'Paper Filing', 'In Progress'),
    ('paper_filing_coa', 'Paper Filing (COA)', 'In Progress'),
    ('state_filing', 'State Filing', 'In Progress'),
    ('signed_docs_uploaded', 'Signed Docs Uploaded', 'In Progress'),
    ('complete', 'Completed', 'Completed'),
    ('amendments', 'Amendments', 'Amendment'),
]
PLACEHOLDER_STAGES = ['SUBMITTED', 'INFO_PENDING', 'DOCS_PENDING', 'PREPARATION', 'REVIEW', 'PAYMENT_PENDING',
                      'PAYMENT_CONFIRMED', 'E_FILING', 'COMPLETED', 'CLOSED', 'AMENDMENT']

# Codes are the values tax_filings.filing_type already stores (client form + admin quick actions).
TAX_TYPES = [
    ('individual', 'Individual (1040)'),
    ('business', 'Business (1065 / 1120)'),
    ('estate', 'Estate'),
    ('non_profit', 'Non-profit'),
    ('amended', 'Amended return'),
]
PLACEHOLDER_TAX_TYPES = ['INDIVIDUAL']

# Union of the lists hardcoded in storage_service, client.py, staff.py and client/documents.html.
CATEGORIES = [
    ('Personal', 'Personal'),
    ('Income', 'Income (W-2, 1099)'),
    ('Employment', 'Employment'),
    ('Investments', 'Investments'),
    ('Deductions', 'Deductions'),
    ('Credits', 'Credits'),
    ('Foreign_Information', 'Foreign info'),
    ('Dependents', 'Dependents'),
    ('Property', 'Property'),
    ('Business', 'Business'),
    ('Other', 'Other'),
]


def _codes(bind, table):
    return {r[0] for r in bind.execute(sa.text(f'SELECT code FROM {table}'))}


def _insert_missing(table, rows, existing, now):
    new_rows = [dict(r, created_at=now, updated_at=now, is_active=True) for r in rows if r['code'] not in existing]
    if new_rows:
        columns = [sa.column(c) for c in new_rows[0]]
        op.bulk_insert(sa.table(table, *columns), new_rows)


def upgrade():
    bind = op.get_bind()
    now = datetime.now(timezone.utc)

    # document_categories (app/main.py create_all may already have made it, empty)
    if not sa.inspect(bind).has_table('document_categories'):
        op.create_table(
            'document_categories',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('code', sa.String(length=50), nullable=False, unique=True),
            sa.Column('name', sa.String(length=80), nullable=False),
            sa.Column('description', sa.String(length=255), nullable=True),
            sa.Column('sort_order', sa.Integer(), nullable=False),
            sa.Column('is_active', sa.Boolean(), nullable=False),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
            sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        )
    _insert_missing('document_categories',
                    [dict(code=c, name=n, sort_order=i + 1) for i, (c, n) in enumerate(CATEGORIES)],
                    _codes(bind, 'document_categories'), now)

    # document_types.category must name a real category
    fks = {fk['name'] for fk in sa.inspect(bind).get_foreign_keys('document_types')}
    if 'fk_document_types_category' not in fks:
        with op.batch_alter_table('document_types') as batch:
            batch.create_foreign_key('fk_document_types_category', 'document_categories', ['category'], ['code'])

    # case_stages: drop unused placeholders, add the real stages
    referenced = {r[0] for r in bind.execute(sa.text(
        'SELECT from_stage_code FROM case_stage_history UNION SELECT to_stage_code FROM case_stage_history'))}
    for code in PLACEHOLDER_STAGES:
        if code not in referenced:
            bind.execute(sa.text('DELETE FROM case_stages WHERE code = :c'), {'c': code})
    _insert_missing('case_stages',
                    [dict(code=c, name=n, client_status=s, is_terminal=(c == 'complete'), sort_order=i + 1)
                     for i, (c, n, s) in enumerate(STAGES)],
                    _codes(bind, 'case_stages'), now)

    # tax_types: drop the unused placeholder, add the real types
    used = {r[0] for r in bind.execute(sa.text('SELECT tax_type_id FROM required_documents'))}
    for code in PLACEHOLDER_TAX_TYPES:
        row = bind.execute(sa.text('SELECT id FROM tax_types WHERE code = :c'), {'c': code}).first()
        if row and row[0] not in used:
            bind.execute(sa.text('DELETE FROM tax_types WHERE id = :i'), {'i': row[0]})
    _insert_missing('tax_types',
                    [dict(code=c, name=n, sort_order=i + 1) for i, (c, n) in enumerate(TAX_TYPES)],
                    _codes(bind, 'tax_types'), now)


def downgrade():
    # Stage and tax-type rows are left in place: the app reads them, and the placeholders
    # they replaced were never used.
    with op.batch_alter_table('document_types') as batch:
        batch.drop_constraint('fk_document_types_category', type_='foreignkey')
    op.drop_table('document_categories')
