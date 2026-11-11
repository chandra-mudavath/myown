"""more case document types (client feedback, Oct 2026) and the app_settings key/value table

Document types follow the client's reference portal: one type per common tax form, grouped
under the existing document_categories. Rows that already exist (by code) are left alone.

app_settings holds small admin-managed values, e.g. the admin sign-up link key.

Revision ID: a2c4e6f8b0d3
Revises: f1a3c5e7b9d2
Create Date: 2026-10-19

"""
from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'a2c4e6f8b0d3'
down_revision = 'f1a3c5e7b9d2'
branch_labels = None
depends_on = None

# (code, name, category). Shown in this order after the existing types.
DOCUMENT_TYPES = [
    ('VISA_I94', 'Visa / I-94 / I-20 / EAD', 'Personal'),
    ('FORM_1099_INT', 'Form 1099-INT (interest)', 'Income'),
    ('FORM_1099_DIV', 'Form 1099-DIV (dividends)', 'Investments'),
    ('FORM_1099_B', 'Form 1099-B / broker statement (stock sales)', 'Investments'),
    ('FORM_1099_NEC_MISC', 'Form 1099-NEC / 1099-MISC (self-employment)', 'Income'),
    ('FORM_1099_R', 'Form 1099-R (IRA / 401(k) distributions)', 'Income'),
    ('FORM_1099_G', 'Form 1099-G (unemployment / state refund)', 'Income'),
    ('FORM_1099_SA', 'Form 1099-SA (HSA distributions)', 'Income'),
    ('FORM_5498_SA', 'Form 5498-SA (HSA contributions)', 'Deductions'),
    ('SCHEDULE_K1', 'Schedule K-1 (partnership / S-corp)', 'Business'),
    ('STOCK_OPTIONS', 'RSU / ESPP / stock option statements', 'Investments'),
    ('DIGITAL_ASSETS', 'Crypto / digital asset statement', 'Investments'),
    ('FORM_1098_T', 'Form 1098-T (tuition)', 'Credits'),
    ('FORM_1098_E', 'Form 1098-E (student loan interest)', 'Deductions'),
    ('FORM_1095_A', 'Form 1095-A (Marketplace insurance)', 'Credits'),
    ('FORM_1095_BC', 'Form 1095-B / 1095-C (health coverage)', 'Other'),
    ('PROPERTY_TAX', 'Property tax receipts', 'Property'),
    ('RENTAL_STATEMENT', 'Rental income & expenses statement', 'Property'),
    ('CHARITY_RECEIPTS', 'Charitable donation receipts', 'Deductions'),
    ('MEDICAL_RECEIPTS', 'Medical expense receipts', 'Deductions'),
    ('CHILDCARE_RECEIPTS', 'Childcare / dependent care receipts', 'Dependents'),
    ('INDIA_FORM_16', 'Form 16 / India salary statement', 'Foreign_Information'),
    ('FOREIGN_BANK', 'Foreign bank statements (FBAR / FATCA)', 'Foreign_Information'),
    ('FOREIGN_TAX_PAID', 'Proof of foreign tax paid', 'Foreign_Information'),
    ('ENERGY_CREDIT', 'Energy-saving purchase receipts (EV, solar)', 'Credits'),
    ('STATE_HEALTH_1099_HC', 'Form 1099-HC (Massachusetts health coverage)', 'Other'),
]


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # main.py's create_all may already have made this table on a fresh database.
    if 'app_settings' not in inspector.get_table_names():
        op.create_table(
            'app_settings',
            sa.Column('key', sa.String(80), primary_key=True),
            sa.Column('value', sa.Text(), nullable=True),
            sa.Column('updated_by_account_id', sa.String(36), nullable=True),
            sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        )

    types = sa.table(
        'document_types',
        sa.column('code', sa.String), sa.column('name', sa.String), sa.column('category', sa.String),
        sa.column('applies_to', sa.String), sa.column('sort_order', sa.Integer), sa.column('is_active', sa.Boolean),
        sa.column('created_at', sa.DateTime), sa.column('updated_at', sa.DateTime),
    )
    existing = {code for (code,) in bind.execute(sa.text('SELECT code FROM document_types'))}
    start = bind.execute(sa.text('SELECT COALESCE(MAX(sort_order), 0) FROM document_types')).scalar() or 0
    now = datetime.now(timezone.utc)
    rows = [
        {'code': code, 'name': name, 'category': category, 'applies_to': 'CASE', 'sort_order': start + i + 1,
         'is_active': True, 'created_at': now, 'updated_at': now}
        for i, (code, name, category) in enumerate(DOCUMENT_TYPES) if code not in existing
    ]
    if rows:
        op.bulk_insert(types, rows)


def downgrade():
    bind = op.get_bind()
    codes = [code for code, _, _ in DOCUMENT_TYPES]
    bind.execute(sa.text('DELETE FROM document_types WHERE code IN :codes').bindparams(sa.bindparam('codes', expanding=True)),
                 {'codes': codes})
    op.drop_table('app_settings')
