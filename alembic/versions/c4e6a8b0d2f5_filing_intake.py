"""filing intake: saved Tax Notes and bank details per filing, document type on uploads, mutual funds type

Revision ID: c4e6a8b0d2f5
Revises: b3d5f7a9c1e4
Create Date: 2026-10-21

"""
from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'c4e6a8b0d2f5'
down_revision = 'b3d5f7a9c1e4'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    # main.py's create_all may already have made the table on a fresh database.
    if 'filing_intake' not in inspector.get_table_names():
        op.create_table(
            'filing_intake',
            sa.Column('id', sa.String(36), primary_key=True),
            sa.Column('filing_id', sa.String(36), sa.ForeignKey('tax_filings.id', ondelete='CASCADE'), nullable=False),
            sa.Column('section', sa.String(20), nullable=False),
            sa.Column('data', sa.JSON(), nullable=False),
            sa.Column('submitted_at', sa.DateTime(timezone=True), nullable=True),
            sa.Column('updated_by_account_id', sa.String(36), nullable=True),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
            sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint('filing_id', 'section', name='uq_filing_intake_section'),
        )
        op.create_index('ix_filing_intake_filing_id', 'filing_intake', ['filing_id'])

    doc_cols = {c['name'] for c in inspector.get_columns('filing_documents')}
    if 'document_type' not in doc_cols:
        op.add_column('filing_documents', sa.Column('document_type', sa.String(50), nullable=True))

    if not bind.execute(sa.text("SELECT 1 FROM document_types WHERE code = 'MF_SUMMARY'")).first():
        start = bind.execute(sa.text('SELECT COALESCE(MAX(sort_order), 0) FROM document_types')).scalar() or 0
        now = datetime.now(timezone.utc)
        types = sa.table('document_types', sa.column('code', sa.String), sa.column('name', sa.String), sa.column('category', sa.String),
                         sa.column('applies_to', sa.String), sa.column('sort_order', sa.Integer), sa.column('is_active', sa.Boolean),
                         sa.column('created_at', sa.DateTime), sa.column('updated_at', sa.DateTime))
        op.bulk_insert(types, [{'code': 'MF_SUMMARY', 'name': 'Foreign mutual funds summary (Excel)', 'category': 'Foreign_Information',
                                'applies_to': 'CASE', 'sort_order': start + 1, 'is_active': True, 'created_at': now, 'updated_at': now}])


def downgrade():
    bind = op.get_bind()
    bind.execute(sa.text("DELETE FROM document_types WHERE code = 'MF_SUMMARY'"))
    op.drop_column('filing_documents', 'document_type')
    op.drop_table('filing_intake')
