"""HR becomes its own account type: AccountType.HR, name columns on hr, Staff[HR] moved over

- auth_accounts.account_type gains 'HR'
- hr gains first_name, last_name, phone, profile_picture (same as admins, until user_profiles)
- every staff row with role HR becomes an hr row and its login becomes an HR account. The staff
  row is removed when nothing else points at it (HR never had cases); otherwise it stays, unused.

Revision ID: a4c6e8f0b2d5
Revises: f3a5c7e9b1d4
Create Date: 2026-11-10

"""
import uuid
from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa

revision = 'a4c6e8f0b2d5'
down_revision = 'f3a5c7e9b1d4'
branch_labels = None
depends_on = None

OLD_TYPES = ('CLIENT', 'STAFF', 'ADMIN')
NEW_TYPES = ('CLIENT', 'STAFF', 'ADMIN', 'HR')
HR_COLUMNS = [
    sa.Column('first_name', sa.String(15), nullable=False, server_default=''),
    sa.Column('last_name', sa.String(15), nullable=True),
    sa.Column('phone', sa.String(20), nullable=True),
    sa.Column('profile_picture', sa.String(255), nullable=True),
]
# Tables that point at staff.id; a staff row is only removed when none of them do
STAFF_REFERENCES = [('case_assignments', 'staff_id'), ('staff_role_assignments', 'staff_id'),
                    ('staff_employment_details', 'staff_id'), ('staff_employment_details', 'reporting_manager_staff_id'),
                    ('staff_documents', 'staff_id'), ('staff_salary_history', 'staff_id'),
                    ('referral_leads', 'assigned_staff_id')]


def _account_type_enum():
    bind = op.get_bind()
    if bind.dialect.name == 'mysql':
        op.alter_column('auth_accounts', 'account_type', existing_type=sa.Enum(*OLD_TYPES, name='accounttype'),
                        type_=sa.Enum(*NEW_TYPES, name='accounttype'), existing_nullable=False)
    elif bind.dialect.name == 'postgresql':
        with op.get_context().autocommit_block():
            op.execute("ALTER TYPE accounttype ADD VALUE IF NOT EXISTS 'HR'")
    # SQLite stores the enum as plain text: nothing to change


def _next_hr_number(bind, tables) -> str:
    seq = sa.table('id_sequences', sa.column('name'), sa.column('prefix'), sa.column('width'),
                   sa.column('current_value'), sa.column('updated_at'))
    now = datetime.now(timezone.utc)
    if 'id_sequences' in tables and bind.execute(sa.select(seq.c.current_value).where(seq.c.name == 'HR')).first():
        bind.execute(seq.update().where(seq.c.name == 'HR').values(current_value=seq.c.current_value + 1, updated_at=now))
        value = bind.execute(sa.select(seq.c.current_value).where(seq.c.name == 'HR')).scalar()
    else:
        used = [n for (n,) in bind.execute(sa.text("SELECT hr_number FROM hr WHERE hr_number LIKE 'HR-%'"))]
        value = max((int(n[3:]) for n in used if n[3:].isdigit()), default=0) + 1
        if 'id_sequences' in tables:
            bind.execute(seq.insert().values(name='HR', prefix='HR-', width=8, current_value=value, updated_at=now))
    return f"HR-{value:08d}"


def _move_staff_hr(bind, tables):
    rows = bind.execute(sa.text(
        "SELECT id, account_id, first_name, last_name, phone, profile_picture FROM staff WHERE role = 'HR'")).fetchall()
    now = datetime.now(timezone.utc)
    for staff_id, account_id, first, last, phone, picture in rows:
        if not bind.execute(sa.text("SELECT 1 FROM hr WHERE account_id = :a"), {"a": account_id}).first():
            bind.execute(sa.text(
                "INSERT INTO hr (id, hr_number, account_id, first_name, last_name, phone, profile_picture, created_at, updated_at) "
                "VALUES (:id, :num, :a, :f, :l, :p, :pic, :now, :now)"),
                {"id": str(uuid.uuid4()), "num": _next_hr_number(bind, tables), "a": account_id, "f": first or '',
                 "l": last, "p": phone, "pic": picture, "now": now})
        bind.execute(sa.text("UPDATE auth_accounts SET account_type = 'HR' WHERE id = :a"), {"a": account_id})
        referenced = any(
            bind.execute(sa.text(f"SELECT 1 FROM {table} WHERE {column} = :s"), {"s": staff_id}).first()
            for table, column in STAFF_REFERENCES if table in tables)
        if not referenced:
            bind.execute(sa.text("DELETE FROM staff WHERE id = :s"), {"s": staff_id})


def upgrade():
    _account_type_enum()
    bind = op.get_bind()
    tables = set(sa.inspect(bind).get_table_names())
    if 'hr' in tables:  # main.py's create_all may already have made it with the new columns
        have = {c['name'] for c in sa.inspect(bind).get_columns('hr')}
        for column in HR_COLUMNS:
            if column.name not in have:
                op.add_column('hr', column.copy())
    if 'hr' in tables and 'staff' in tables:
        _move_staff_hr(bind, tables)


def downgrade():
    # HR accounts can't be turned back into staff rows automatically; only the columns are dropped.
    for name in ('profile_picture', 'phone', 'last_name', 'first_name'):
        op.drop_column('hr', name)
