"""add lookup tables and the remaining revision 2 tables

Adds the 23 tables from Data Models/Final-Data-Model.md (revision 2) that have no
model yet, and seeds the lookup tables. Purely additive: existing tables and
columns are not touched (moving them onto the lookups is a later step).

Revision ID: d4e6a8c0f2b3
Revises: c3d5f7a9b1e2
Create Date: 2026-10-01

"""
from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'd4e6a8c0f2b3'
down_revision = 'c3d5f7a9b1e2'
branch_labels = None
depends_on = None


# Creation order respects foreign keys; downgrade drops in reverse.
TABLES = [
    'account_types', 'staff_roles', 'tax_years', 'tax_types', 'document_types', 'case_stages',
    'required_documents', 'user_profiles', 'hr',
    'staff_role_assignments', 'staff_employment_details', 'staff_documents', 'staff_salary_history',
    'case_assignments', 'case_stage_history', 'irs_submissions', 'acknowledgments',
    'services', 'case_services', 'invoices', 'payments',
    'audit_logs', 'notifications',
]


def _ts(name, nullable=False):
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def _lookup_base(name_length=80):
    return [
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('name', sa.String(length=name_length), nullable=False),
        sa.Column('description', sa.String(length=255), nullable=True),
        sa.Column('sort_order', sa.Integer(), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        _ts('created_at'),
        _ts('updated_at'),
    ]


def _uuid_pk():
    return sa.Column('id', sa.String(length=36), primary_key=True)


def _account_fk(name, nullable=True, ondelete='SET NULL'):
    return sa.Column(name, sa.String(length=36), sa.ForeignKey('auth_accounts.id', ondelete=ondelete), nullable=nullable)


def _define(name):
    """op.create_table arguments for one table, matching app/models and schema.sql."""
    if name == 'account_types':
        return [*_lookup_base(),
                sa.Column('code', sa.String(length=30), nullable=False, unique=True),
                sa.Column('number_prefix', sa.String(length=10), nullable=False, unique=True),
                sa.Column('portal_path', sa.String(length=80), nullable=False),
                sa.Column('has_case_access', sa.Boolean(), nullable=False)]
    if name == 'staff_roles':
        return [*_lookup_base(),
                sa.Column('code', sa.String(length=30), nullable=False, unique=True),
                sa.Column('is_case_role', sa.Boolean(), nullable=False)]
    if name == 'tax_years':
        return [*_lookup_base(),
                sa.Column('year', sa.Integer(), nullable=False, unique=True),
                sa.Column('is_open', sa.Boolean(), nullable=False),
                sa.Column('is_default', sa.Boolean(), nullable=False),
                sa.Column('filing_deadline', sa.Date(), nullable=True)]
    if name == 'tax_types':
        return [*_lookup_base(),
                sa.Column('code', sa.String(length=50), nullable=False, unique=True)]
    if name == 'document_types':
        return [*_lookup_base(name_length=120),
                sa.Column('code', sa.String(length=50), nullable=False, unique=True),
                sa.Column('category', sa.String(length=50), nullable=False),
                sa.Column('applies_to', sa.String(length=10), nullable=False),
                sa.Column('allowed_mime_types', sa.String(length=255), nullable=True),
                sa.Column('max_file_mb', sa.Integer(), nullable=True),
                sa.CheckConstraint("applies_to IN ('CASE', 'STAFF')", name='ck_document_types_applies_to')]
    if name == 'case_stages':
        return [*_lookup_base(),
                sa.Column('code', sa.String(length=50), nullable=False, unique=True),
                sa.Column('client_status', sa.String(length=20), nullable=False),
                sa.Column('is_terminal', sa.Boolean(), nullable=False),
                sa.CheckConstraint(
                    "client_status IN ('Submitted', 'In Progress', 'Completed', 'Closed', 'Amendment')",
                    name='ck_case_stages_client_status')]
    if name == 'required_documents':
        return [_uuid_pk(),
                sa.Column('tax_year_id', sa.Integer(), sa.ForeignKey('tax_years.id'), nullable=False),
                sa.Column('tax_type_id', sa.Integer(), sa.ForeignKey('tax_types.id'), nullable=False),
                sa.Column('document_type_id', sa.Integer(), sa.ForeignKey('document_types.id'), nullable=False),
                sa.Column('is_required', sa.Boolean(), nullable=False),
                _ts('created_at'),
                sa.UniqueConstraint('tax_year_id', 'tax_type_id', 'document_type_id', name='uq_required_documents')]
    if name == 'user_profiles':
        return [_uuid_pk(),
                sa.Column('account_id', sa.String(length=36), sa.ForeignKey('auth_accounts.id', ondelete='CASCADE'), nullable=False, unique=True),
                sa.Column('first_name', sa.String(length=50), nullable=False),
                sa.Column('last_name', sa.String(length=50), nullable=True),
                sa.Column('phone', sa.String(length=25), nullable=True),
                sa.Column('profile_picture', sa.String(length=255), nullable=True),
                sa.Column('date_of_birth', sa.Date(), nullable=True),
                sa.Column('address_line_1', sa.String(length=120), nullable=True),
                sa.Column('address_line_2', sa.String(length=120), nullable=True),
                sa.Column('city', sa.String(length=80), nullable=True),
                sa.Column('state_province', sa.String(length=80), nullable=True),
                sa.Column('postal_code', sa.String(length=20), nullable=True),
                sa.Column('country', sa.String(length=80), nullable=True),
                _ts('created_at'),
                _ts('updated_at')]
    if name == 'hr':
        return [_uuid_pk(),
                sa.Column('hr_number', sa.String(length=20), nullable=False),
                sa.Column('account_id', sa.String(length=36), sa.ForeignKey('auth_accounts.id', ondelete='CASCADE'), nullable=False, unique=True),
                sa.Column('department', sa.String(length=80), nullable=True),
                _ts('created_at'),
                _ts('updated_at')]
    if name == 'staff_role_assignments':
        return [_uuid_pk(),
                sa.Column('staff_id', sa.String(length=36), sa.ForeignKey('staff.id', ondelete='CASCADE'), nullable=False),
                sa.Column('role_id', sa.Integer(), sa.ForeignKey('staff_roles.id'), nullable=False),
                sa.Column('is_primary', sa.Boolean(), nullable=False),
                _account_fk('assigned_by_account_id'),
                _ts('assigned_at'),
                sa.UniqueConstraint('staff_id', 'role_id', name='uq_staff_role')]
    if name == 'staff_employment_details':
        return [_uuid_pk(),
                sa.Column('staff_id', sa.String(length=36), sa.ForeignKey('staff.id', ondelete='CASCADE'), nullable=False, unique=True),
                sa.Column('employee_number', sa.String(length=30), nullable=False, unique=True),
                sa.Column('date_of_joining', sa.Date(), nullable=True),
                sa.Column('employment_type', sa.String(length=30), nullable=True),
                sa.Column('department', sa.String(length=80), nullable=True),
                sa.Column('designation', sa.String(length=80), nullable=True),
                sa.Column('reporting_manager_staff_id', sa.String(length=36), sa.ForeignKey('staff.id', ondelete='SET NULL'), nullable=True),
                sa.Column('employment_status', sa.String(length=30), nullable=False),
                sa.Column('termination_date', sa.Date(), nullable=True),
                sa.Column('current_salary', sa.Numeric(12, 2), nullable=True),
                _account_fk('updated_by_account_id'),
                _ts('updated_at')]
    if name == 'staff_documents':
        return [_uuid_pk(),
                sa.Column('staff_id', sa.String(length=36), sa.ForeignKey('staff.id', ondelete='CASCADE'), nullable=False),
                sa.Column('document_type_id', sa.Integer(), sa.ForeignKey('document_types.id'), nullable=False),
                sa.Column('original_filename', sa.String(length=255), nullable=False),
                sa.Column('stored_filename', sa.String(length=255), nullable=False),
                sa.Column('file_path', sa.String(length=500), nullable=False),
                sa.Column('mime_type', sa.String(length=100), nullable=False),
                sa.Column('file_size', sa.Integer(), nullable=False),
                _account_fk('uploaded_by_account_id'),
                _ts('uploaded_at'),
                sa.Column('expiry_date', sa.Date(), nullable=True),
                sa.Column('notes', sa.Text(), nullable=True)]
    if name == 'staff_salary_history':
        return [_uuid_pk(),
                sa.Column('staff_id', sa.String(length=36), sa.ForeignKey('staff.id', ondelete='CASCADE'), nullable=False),
                sa.Column('effective_date', sa.Date(), nullable=False),
                sa.Column('previous_salary', sa.Numeric(12, 2), nullable=True),
                sa.Column('new_salary', sa.Numeric(12, 2), nullable=False),
                sa.Column('hike_percentage', sa.Numeric(5, 2), nullable=True),
                sa.Column('reason', sa.String(length=255), nullable=True),
                _account_fk('approved_by_account_id'),
                _ts('created_at')]
    if name == 'case_assignments':
        return [_uuid_pk(),
                sa.Column('case_id', sa.String(length=36), sa.ForeignKey('tax_filings.id', ondelete='CASCADE'), nullable=False),
                sa.Column('staff_id', sa.String(length=36), sa.ForeignKey('staff.id'), nullable=False),
                sa.Column('role_id', sa.Integer(), sa.ForeignKey('staff_roles.id'), nullable=False),
                _account_fk('assigned_by_account_id'),
                _ts('assigned_at'),
                _ts('unassigned_at', nullable=True),
                sa.Column('status', sa.String(length=10), nullable=False),
                sa.CheckConstraint("status IN ('active', 'ended')", name='ck_ca_status')]
    if name == 'case_stage_history':
        return [_uuid_pk(),
                sa.Column('case_id', sa.String(length=36), sa.ForeignKey('tax_filings.id', ondelete='CASCADE'), nullable=False),
                sa.Column('from_stage_code', sa.String(length=50), sa.ForeignKey('case_stages.code'), nullable=True),
                sa.Column('to_stage_code', sa.String(length=50), sa.ForeignKey('case_stages.code'), nullable=False),
                _account_fk('changed_by_account_id'),
                sa.Column('comment', sa.Text(), nullable=True),
                _ts('created_at')]
    if name == 'irs_submissions':
        return [_uuid_pk(),
                sa.Column('submission_number', sa.String(length=30), nullable=False, unique=True),
                sa.Column('case_id', sa.String(length=36), sa.ForeignKey('tax_filings.id', ondelete='CASCADE'), nullable=False),
                _account_fk('submitted_by_account_id'),
                sa.Column('irs_submission_id', sa.String(length=50), nullable=True),
                sa.Column('status', sa.String(length=20), nullable=False),
                _ts('submitted_at', nullable=True),
                _ts('created_at'),
                _ts('updated_at'),
                sa.CheckConstraint("status IN ('pending', 'submitted', 'accepted', 'rejected')", name='ck_irs_sub_status')]
    if name == 'acknowledgments':
        return [_uuid_pk(),
                sa.Column('ack_number', sa.String(length=30), nullable=False, unique=True),
                sa.Column('submission_id', sa.String(length=36), sa.ForeignKey('irs_submissions.id', ondelete='CASCADE'), nullable=False),
                _account_fk('recorded_by_account_id'),
                sa.Column('irs_ack_number', sa.String(length=50), nullable=True),
                sa.Column('status', sa.String(length=20), nullable=False),
                _ts('received_at', nullable=True),
                sa.Column('message', sa.Text(), nullable=True),
                _ts('created_at'),
                sa.CheckConstraint("status IN ('accepted', 'rejected')", name='ck_ack_status')]
    if name == 'services':
        return [_uuid_pk(),
                sa.Column('service_code', sa.String(length=30), nullable=False, unique=True),
                sa.Column('name', sa.String(length=120), nullable=False),
                sa.Column('description', sa.Text(), nullable=True),
                sa.Column('base_price', sa.Numeric(12, 2), nullable=False),
                sa.Column('is_active', sa.Boolean(), nullable=False),
                _ts('created_at'),
                _ts('updated_at')]
    if name == 'case_services':
        return [_uuid_pk(),
                sa.Column('case_id', sa.String(length=36), sa.ForeignKey('tax_filings.id', ondelete='CASCADE'), nullable=False),
                sa.Column('service_id', sa.String(length=36), sa.ForeignKey('services.id'), nullable=False),
                sa.Column('quantity', sa.Integer(), nullable=False),
                sa.Column('unit_price', sa.Numeric(12, 2), nullable=False),
                sa.Column('total_amount', sa.Numeric(12, 2), nullable=False),
                _ts('created_at'),
                sa.CheckConstraint('quantity > 0', name='ck_cs_quantity')]
    if name == 'invoices':
        return [_uuid_pk(),
                sa.Column('invoice_number', sa.String(length=30), nullable=False, unique=True),
                sa.Column('client_id', sa.String(length=36), sa.ForeignKey('clients.id'), nullable=False),
                sa.Column('case_id', sa.String(length=36), sa.ForeignKey('tax_filings.id'), nullable=False),
                sa.Column('subtotal', sa.Numeric(12, 2), nullable=False),
                sa.Column('discount_amount', sa.Numeric(12, 2), nullable=False),
                sa.Column('tax_amount', sa.Numeric(12, 2), nullable=False),
                sa.Column('total_amount', sa.Numeric(12, 2), nullable=False),
                sa.Column('status', sa.String(length=20), nullable=False),
                sa.Column('invoice_date', sa.Date(), nullable=False),
                sa.Column('due_date', sa.Date(), nullable=True),
                _ts('created_at'),
                sa.CheckConstraint("status IN ('draft', 'issued', 'paid', 'partially_paid', 'void')", name='ck_inv_status')]
    if name == 'payments':
        return [_uuid_pk(),
                sa.Column('payment_number', sa.String(length=30), nullable=False, unique=True),
                sa.Column('invoice_id', sa.String(length=36), sa.ForeignKey('invoices.id'), nullable=False),
                sa.Column('client_id', sa.String(length=36), sa.ForeignKey('clients.id'), nullable=False),
                sa.Column('amount', sa.Numeric(12, 2), nullable=False),
                sa.Column('payment_method', sa.String(length=30), nullable=False),
                sa.Column('transaction_reference', sa.String(length=100), nullable=True),
                sa.Column('status', sa.String(length=20), nullable=False),
                _ts('paid_at', nullable=True),
                _ts('created_at'),
                sa.CheckConstraint("status IN ('pending', 'succeeded', 'failed', 'refunded')", name='ck_pay_status'),
                sa.CheckConstraint('amount > 0', name='ck_pay_amount')]
    if name == 'audit_logs':
        return [_uuid_pk(),
                _account_fk('actor_account_id'),
                sa.Column('action', sa.String(length=50), nullable=False),
                sa.Column('entity_type', sa.String(length=50), nullable=False),
                sa.Column('entity_id', sa.String(length=36), nullable=False),
                sa.Column('old_values', sa.JSON(), nullable=True),
                sa.Column('new_values', sa.JSON(), nullable=True),
                sa.Column('ip_address', sa.String(length=45), nullable=True),
                _ts('created_at')]
    if name == 'notifications':
        return [_uuid_pk(),
                _account_fk('recipient_account_id', nullable=False, ondelete='CASCADE'),
                sa.Column('type', sa.String(length=50), nullable=False),
                sa.Column('title', sa.String(length=150), nullable=False),
                sa.Column('message', sa.Text(), nullable=True),
                sa.Column('entity_type', sa.String(length=50), nullable=True),
                sa.Column('entity_id', sa.String(length=36), nullable=True),
                sa.Column('is_read', sa.Boolean(), nullable=False),
                _ts('created_at'),
                _ts('read_at', nullable=True)]
    raise KeyError(name)


# Indexes named as SQLAlchemy names them for index=True / Index() in the models.
INDEXES = {
    'hr': [('ix_hr_hr_number', ['hr_number'], True)],
    'staff_role_assignments': [('ix_staff_role_assignments_staff_id', ['staff_id'], False)],
    'staff_documents': [('ix_staff_documents_staff_id', ['staff_id'], False)],
    'staff_salary_history': [('ix_staff_salary_history_staff_id', ['staff_id'], False)],
    'case_assignments': [('ix_case_assignments_case_id', ['case_id'], False),
                         ('ix_case_assignments_staff_status', ['staff_id', 'status'], False)],
    'case_stage_history': [('ix_case_stage_history_case_id', ['case_id', 'created_at'], False)],
    'irs_submissions': [('ix_irs_submissions_case_id', ['case_id'], False)],
    'acknowledgments': [('ix_acknowledgments_submission_id', ['submission_id'], False)],
    'case_services': [('ix_case_services_case_id', ['case_id'], False)],
    'invoices': [('ix_invoices_client_id', ['client_id'], False),
                 ('ix_invoices_case_id', ['case_id'], False)],
    'payments': [('ix_payments_invoice_id', ['invoice_id'], False)],
    'audit_logs': [('ix_audit_logs_entity', ['entity_type', 'entity_id'], False),
                   ('ix_audit_logs_actor', ['actor_account_id', 'created_at'], False)],
    'notifications': [('ix_notifications_recipient', ['recipient_account_id', 'is_read', 'created_at'], False)],
}


# Same values as the seed section of Data Models/schema.sql.
def _seed_rows():
    return {
        'account_types': [
            dict(id=1, code='CLIENT', name='Client', number_prefix='CLT', portal_path='/client', has_case_access=True, sort_order=1),
            dict(id=2, code='STAFF', name='Staff', number_prefix='STF', portal_path='/staff', has_case_access=True, sort_order=2),
            dict(id=3, code='ADMIN', name='Admin', number_prefix='ADM', portal_path='/admin', has_case_access=True, sort_order=3),
            dict(id=4, code='HR', name='HR', number_prefix='HR', portal_path='/hr', has_case_access=False, sort_order=4),
        ],
        'staff_roles': [
            dict(id=1, code='INITIATOR', name='Initiator', description='Checks intake and requests missing information', is_case_role=True, sort_order=1),
            dict(id=2, code='PREPARER', name='Preparer', description='Prepares the return', is_case_role=True, sort_order=2),
            dict(id=3, code='REVIEWER', name='Reviewer', description='Reviews the prepared return', is_case_role=True, sort_order=3),
            dict(id=4, code='MANAGER', name='Manager', description='Oversees cases and assignments', is_case_role=True, sort_order=4),
        ],
        'tax_years': [
            dict(id=i + 1, year=year, name=f'Tax year {year}', is_open=True, is_default=(year == 2025), sort_order=i + 1)
            for i, year in enumerate([2026, 2025, 2024, 2023, 2022])
        ],
        'tax_types': [
            dict(id=1, code='INDIVIDUAL', name='Individual', sort_order=1),
        ],
        'document_types': [
            dict(id=i + 1, code=code, name=name, category=category, applies_to=applies_to, sort_order=i + 1)
            for i, (code, name, category, applies_to) in enumerate([
                ('PASSPORT', 'Passport / ID', 'Personal', 'CASE'),
                ('SSN_ITIN', 'SSN or ITIN letter', 'Personal', 'CASE'),
                ('W2', 'Form W-2', 'Income', 'CASE'),
                ('FORM_1099', 'Form 1099', 'Income', 'CASE'),
                ('FORM_1098', 'Form 1098 (mortgage)', 'Deductions', 'CASE'),
                ('PRIOR_RETURN', 'Prior year return', 'Other', 'CASE'),
                ('OTHER', 'Other document', 'Other', 'CASE'),
                ('OFFER_LETTER', 'Offer letter', 'Employment', 'STAFF'),
                ('ID_PROOF', 'ID proof', 'Employment', 'STAFF'),
                ('CONTRACT', 'Employment contract', 'Employment', 'STAFF'),
            ])
        ],
        'case_stages': [
            dict(id=i + 1, code=code, name=name, client_status=client_status, is_terminal=terminal, sort_order=i + 1)
            for i, (code, name, client_status, terminal) in enumerate([
                ('SUBMITTED', 'Submitted', 'Submitted', False),
                ('INFO_PENDING', 'Info pending', 'In Progress', False),
                ('DOCS_PENDING', 'Docs pending', 'In Progress', False),
                ('PREPARATION', 'Preparation', 'In Progress', False),
                ('REVIEW', 'Review', 'In Progress', False),
                ('PAYMENT_PENDING', 'Payment pending', 'In Progress', False),
                ('PAYMENT_CONFIRMED', 'Payment confirmed', 'In Progress', False),
                ('E_FILING', 'E-filing', 'In Progress', False),
                ('COMPLETED', 'Completed', 'Completed', True),
                ('CLOSED', 'Closed', 'Closed', True),
                ('AMENDMENT', 'Amendment', 'Amendment', False),
            ])
        ],
    }


def _seed(bind):
    now = datetime.now(timezone.utc)
    for table_name, rows in _seed_rows().items():
        table = sa.table(table_name, *[sa.column(c) for c in {k for r in rows for k in r} | {'description', 'is_active', 'created_at', 'updated_at'}])
        if bind.execute(sa.select(sa.func.count()).select_from(table)).scalar():
            continue  # admins may already have edited this list; never overwrite it
        op.bulk_insert(table, [{'description': None, 'is_active': True, 'created_at': now, 'updated_at': now, **r} for r in rows])
        if bind.dialect.name == 'postgresql':
            # Explicit ids don't advance the serial sequence; move it past the seeded rows.
            op.execute(f"SELECT setval(pg_get_serial_sequence('{table_name}', 'id'), (SELECT MAX(id) FROM {table_name}))")


def upgrade():
    # app/main.py runs Base.metadata.create_all() on startup, so any of these tables may already
    # exist (empty) if the server started before this migration ran.
    bind = op.get_bind()
    insp = sa.inspect(bind)
    for name in TABLES:
        if insp.has_table(name):
            continue
        op.create_table(name, *_define(name))
        for index_name, columns, unique in INDEXES.get(name, []):
            op.create_index(index_name, name, columns, unique=unique)
    _seed(bind)


def downgrade():
    for name in reversed(TABLES):
        op.drop_table(name)
