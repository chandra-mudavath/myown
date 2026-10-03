"""invoice lines: case_services.invoice_id

Revision ID: e8f0a2b4c6d9
Revises: d7e9f1a3b5c8
Create Date: 2026-10-03

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'e8f0a2b4c6d9'
down_revision = 'd7e9f1a3b5c8'
branch_labels = None
depends_on = None


def upgrade():
    # Each invoice line (case_services row) records the invoice that billed it, so a case can have
    # several invoices without billing a line twice. Nullable: existing rows have no invoice.
    insp = sa.inspect(op.get_bind())
    if 'invoice_id' not in {c['name'] for c in insp.get_columns('case_services')}:
        op.add_column('case_services', sa.Column('invoice_id', sa.String(36), nullable=True))
        op.create_foreign_key('fk_cs_invoice', 'case_services', 'invoices', ['invoice_id'], ['id'], ondelete='CASCADE')
        op.create_index('ix_case_services_invoice_id', 'case_services', ['invoice_id'])


def downgrade():
    op.drop_index('ix_case_services_invoice_id', table_name='case_services')
    op.drop_constraint('fk_cs_invoice', 'case_services', type_='foreignkey')
    op.drop_column('case_services', 'invoice_id')
