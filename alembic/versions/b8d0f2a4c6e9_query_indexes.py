"""indexes for the queries the app runs (dashboards, case lists, feeds, search)

Each index matches a real query; the comment says which. Every index is created only
when missing, because app/main.py create_all() also creates the ones declared on models.

On PostgreSQL (production) this also adds trigram (pg_trgm) indexes so the
"contains" searches (ILIKE '%term%') on case number, email and names use an index.
They are skipped, with a message, when the pg_trgm extension can't be enabled.
MySQL (local) has no equivalent for '%term%' searches; it scans, which is fine at
local data sizes.

Revision ID: b8d0f2a4c6e9
Revises: a7c9e1b3d5f8
Create Date: 2026-10-02

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'b8d0f2a4c6e9'
down_revision = 'a7c9e1b3d5f8'
branch_labels = None
depends_on = None


# (table, index name, columns). Mirrors the Index(...) entries in app/models.
INDEXES = [
    # Client dashboard + filings page: WHERE client_id = ? AND tax_year = ? ORDER BY created_at DESC
    ('tax_filings', 'ix_tax_filings_client_year', ['client_id', 'tax_year', 'created_at']),
    # Staff stage sidebar counts (GROUP BY status), stage filter, admin status filter + newest first
    ('tax_filings', 'ix_tax_filings_status_created', ['status', 'created_at']),
    # Staff case list filtered by year (and stage); distinct year list
    ('tax_filings', 'ix_tax_filings_year_status', ['tax_year', 'status']),
    # Case lists sorted newest first; notification feed; "new since last visit" counts
    ('tax_filings', 'ix_tax_filings_created', ['created_at']),
    # Admin dashboard "recently updated"
    ('tax_filings', 'ix_tax_filings_updated', ['updated_at']),

    # Latest version of each document on a case, newest first
    ('filing_documents', 'ix_filing_documents_filing_latest', ['filing_id', 'is_latest', 'uploaded_at']),
    # Version history of one document
    ('filing_documents', 'ix_filing_documents_group_latest', ['document_group_id', 'is_latest']),
    # Staff notification feed + unread count: client uploads, newest first
    ('filing_documents', 'ix_filing_documents_uploader', ['uploaded_by_type', 'uploaded_at']),

    # Review thread of a case / of one document, in order
    ('document_comments', 'ix_document_comments_filing_created', ['filing_id', 'created_at']),
    ('document_comments', 'ix_document_comments_group_created', ['document_group_id', 'created_at']),
    # Staff notification feed + unread count: client replies, newest first
    ('document_comments', 'ix_document_comments_author_kind', ['author_type', 'kind', 'created_at']),

    # Client and staff lists, newest first
    ('clients', 'ix_clients_created', ['created_at']),
    ('staff', 'ix_staff_created', ['created_at']),

    # Sign out everywhere: revoke an account's live refresh tokens
    ('auth_refresh_tokens', 'ix_auth_refresh_tokens_account_revoked', ['account_id', 'revoked_at']),

    # Active assignments on a case (who is working it)
    ('case_assignments', 'ix_case_assignments_case_status', ['case_id', 'status']),

    # Billing: unpaid / overdue invoices, revenue by period
    ('invoices', 'ix_invoices_status_due', ['status', 'due_date']),
    ('payments', 'ix_payments_status_paid', ['status', 'paid_at']),

    # HR: documents expiring soon, salary history in date order, staff by employment status
    ('staff_documents', 'ix_staff_documents_expiry', ['expiry_date']),
    ('staff_salary_history', 'ix_staff_salary_history_staff_date', ['staff_id', 'effective_date']),
    ('staff_employment_details', 'ix_staff_employment_status', ['employment_status']),

    # Admin audit view by date range
    ('audit_logs', 'ix_audit_logs_created', ['created_at']),
]

# PostgreSQL only: trigram indexes for ILIKE '%term%' searches (admin and staff search boxes).
TRIGRAM_INDEXES = [
    ('tax_filings', 'case_number'), ('tax_filings', 'email'),
    ('tax_filings', 'first_name'), ('tax_filings', 'last_name'),
    ('clients', 'client_number'), ('clients', 'first_name'), ('clients', 'last_name'),
    ('staff', 'staff_number'), ('staff', 'first_name'), ('staff', 'last_name'),
]


def _trgm_name(table, column):
    return f'ix_{table}_{column}_trgm'


def _existing(bind, table):
    return {i['name'] for i in sa.inspect(bind).get_indexes(table)}


def _enable_pg_trgm(bind):
    """True when pg_trgm is available. A failure (no permission) is rolled back to a savepoint."""
    if bind.execute(sa.text("SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm'")).first():
        return True
    savepoint = bind.begin_nested()
    try:
        bind.execute(sa.text('CREATE EXTENSION IF NOT EXISTS pg_trgm'))
        savepoint.commit()
        return True
    except sa.exc.DBAPIError as exc:
        savepoint.rollback()
        print(f'Skipping trigram search indexes: could not enable pg_trgm ({exc.orig}). '
              'Ask the database admin to run CREATE EXTENSION pg_trgm, then re-run this migration.')
        return False


def upgrade():
    bind = op.get_bind()
    for table, name, columns in INDEXES:
        if name not in _existing(bind, table):
            op.create_index(name, table, columns)

    if bind.dialect.name == 'postgresql' and _enable_pg_trgm(bind):
        for table, column in TRIGRAM_INDEXES:
            name = _trgm_name(table, column)
            if name not in _existing(bind, table):
                op.create_index(name, table, [column], postgresql_using='gin',
                                postgresql_ops={column: 'gin_trgm_ops'})


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        for table, column in TRIGRAM_INDEXES:
            name = _trgm_name(table, column)
            if name in _existing(bind, table):
                op.drop_index(name, table_name=table)
    for table, name, _ in reversed(INDEXES):
        if name in _existing(bind, table):
            op.drop_index(name, table_name=table)
