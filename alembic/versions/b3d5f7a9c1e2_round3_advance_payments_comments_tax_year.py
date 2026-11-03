"""Round 3 demo feedback: advance payments, staff case comments, current tax year default

- payments.invoice_id may be empty (a client paid before an invoice was issued); payments.case_id says which filing
- case_comments: notes staff leave on a case for each other
- tax_years: older databases were seeded with 2025 as the default; make 2026 the default there (a fresh database is
  seeded with 2026 already, so this part does nothing on it)

Revision ID: b3d5f7a9c1e2
Revises: a9c1e3f5b7d0
Create Date: 2026-11-02

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'b3d5f7a9c1e2'
down_revision = 'a9c1e3f5b7d0'
branch_labels = None
depends_on = None

CURRENT_YEAR = 2026
OLD_SEED_DEFAULT = 2025


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # main.py's create_all may already have added these on a fresh database.
    pay_cols = {c['name']: c for c in inspector.get_columns('payments')}
    with op.batch_alter_table('payments') as batch:
        if 'case_id' not in pay_cols:
            batch.add_column(sa.Column('case_id', sa.String(36), nullable=True))
            batch.create_foreign_key('fk_payments_case_id', 'tax_filings', ['case_id'], ['id'], ondelete='SET NULL')
            batch.create_index('ix_payments_case_id', ['case_id'])
        if not pay_cols['invoice_id']['nullable']:
            batch.alter_column('invoice_id', existing_type=sa.String(36), nullable=True)
    # Existing payments belong to their invoice's case.
    bind.execute(sa.text(
        "UPDATE payments SET case_id = (SELECT invoices.case_id FROM invoices WHERE invoices.id = payments.invoice_id) "
        "WHERE case_id IS NULL AND invoice_id IS NOT NULL"))

    if 'case_comments' not in inspector.get_table_names():
        op.create_table(
            'case_comments',
            sa.Column('id', sa.String(36), primary_key=True),
            sa.Column('case_id', sa.String(36), sa.ForeignKey('tax_filings.id', ondelete='CASCADE'), nullable=False),
            sa.Column('author_account_id', sa.String(36), sa.ForeignKey('auth_accounts.id', ondelete='SET NULL'), nullable=True),
            sa.Column('author_name', sa.String(80), nullable=False),
            sa.Column('body', sa.Text(), nullable=False),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index('ix_case_comments_case_created', 'case_comments', ['case_id', 'created_at'])

    # Tax year: open the current year and make it the default, unless an admin already picked another default.
    tax_years = sa.table('tax_years', sa.column('id', sa.Integer), sa.column('year', sa.Integer), sa.column('name', sa.String),
                         sa.column('is_open', sa.Boolean), sa.column('is_default', sa.Boolean),
                         sa.column('is_active', sa.Boolean), sa.column('sort_order', sa.Integer),
                         sa.column('created_at', sa.DateTime), sa.column('updated_at', sa.DateTime))
    rows = {r.year: r for r in bind.execute(sa.select(tax_years.c.year, tax_years.c.is_default, tax_years.c.sort_order))}
    if CURRENT_YEAR not in rows:
        # No explicit id: the database assigns it (an explicit id would leave PostgreSQL's sequence behind)
        next_sort = max((r.sort_order or 0 for r in rows.values()), default=0) + 1
        bind.execute(tax_years.insert().values(year=CURRENT_YEAR, name=f'Tax year {CURRENT_YEAR}', is_open=True,
                                               is_default=False, is_active=True, sort_order=next_sort,
                                               created_at=sa.func.now(), updated_at=sa.func.now()))
    defaults = [y for y, r in rows.items() if r.is_default]
    if not defaults or defaults == [OLD_SEED_DEFAULT]:
        bind.execute(tax_years.update().values(is_default=False))
        bind.execute(tax_years.update().where(tax_years.c.year == CURRENT_YEAR).values(is_default=True, is_open=True, is_active=True))


def downgrade():
    op.drop_index('ix_case_comments_case_created', table_name='case_comments')
    op.drop_table('case_comments')
    with op.batch_alter_table('payments') as batch:
        batch.drop_index('ix_payments_case_id')
        batch.drop_constraint('fk_payments_case_id', type_='foreignkey')
        batch.drop_column('case_id')
        # invoice_id stays nullable: advance payments may exist and can't be given an invoice here.
