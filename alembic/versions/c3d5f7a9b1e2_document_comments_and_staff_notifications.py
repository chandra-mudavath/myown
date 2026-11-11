"""document comments and staff notification read marker

Revision ID: c3d5f7a9b1e2
Revises: b2c4e6f8a0d1
Create Date: 2026-09-28

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'c3d5f7a9b1e2'
down_revision = 'b2c4e6f8a0d1'
branch_labels = None
depends_on = None


def upgrade():
    # app/main.py runs Base.metadata.create_all() on startup, so if the server started before this
    # migration ran, document_comments already exists (create_all never adds the staff column though).
    insp = sa.inspect(op.get_bind())
    if not insp.has_table('document_comments'):
        _create_document_comments()
    if 'notifications_seen_at' not in {c['name'] for c in insp.get_columns('staff')}:
        op.add_column('staff', sa.Column('notifications_seen_at', sa.DateTime(timezone=True), nullable=True))


def _create_document_comments():
    op.create_table(
        'document_comments',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('filing_id', sa.String(length=36), nullable=False),
        sa.Column('document_group_id', sa.String(length=36), nullable=False),
        sa.Column('document_id', sa.String(length=36), nullable=True),
        sa.Column('kind', sa.String(length=20), nullable=False),
        sa.Column('body', sa.Text(), nullable=False),
        sa.Column('author_type', sa.String(length=20), nullable=False),
        sa.Column('author_account_id', sa.String(length=36), nullable=False),
        sa.Column('author_name', sa.String(length=80), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['filing_id'], ['tax_filings.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['document_id'], ['filing_documents.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_document_comments_filing_id'), 'document_comments', ['filing_id'], unique=False)
    op.create_index(op.f('ix_document_comments_document_group_id'), 'document_comments', ['document_group_id'], unique=False)
    op.create_index(op.f('ix_document_comments_created_at'), 'document_comments', ['created_at'], unique=False)


def downgrade():
    op.drop_column('staff', 'notifications_seen_at')
    op.drop_index(op.f('ix_document_comments_created_at'), table_name='document_comments')
    op.drop_index(op.f('ix_document_comments_document_group_id'), table_name='document_comments')
    op.drop_index(op.f('ix_document_comments_filing_id'), table_name='document_comments')
    op.drop_table('document_comments')
