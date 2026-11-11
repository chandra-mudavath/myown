"""HR: leave types and requests, holidays, daily attendance, generated HR documents

Default leave types and the HR staff document types are added by the app the first time HR
opens a page that needs them (app/modules/hr/services/hr_settings.ensure_defaults), so a
fresh database and an upgraded one end up the same.

Revision ID: b6d8f0a2c4e7
Revises: a4c6e8f0b2d5
Create Date: 2026-11-11

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision = 'b6d8f0a2c4e7'
down_revision = 'a4c6e8f0b2d5'
branch_labels = None
depends_on = None


def _created(name: str) -> bool:
    # main.py's create_all may already have made it on startup
    return name in sa.inspect(op.get_bind()).get_table_names()


def _stamp():
    return [sa.Column('created_at', sa.DateTime(timezone=True), nullable=False)]


def upgrade():
    if not _created('leave_types'):
        op.create_table(
            'leave_types',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('code', sa.String(30), nullable=False, unique=True),
            sa.Column('name', sa.String(80), nullable=False),
            sa.Column('description', sa.String(255), nullable=True),
            sa.Column('sort_order', sa.Integer(), nullable=False),
            sa.Column('is_active', sa.Boolean(), nullable=False),
            sa.Column('days_per_year', sa.Integer(), nullable=False),
            sa.Column('is_paid', sa.Boolean(), nullable=False),
            sa.Column('carry_over', sa.Boolean(), nullable=False),
            sa.Column('max_carry_over', sa.Integer(), nullable=True),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
            sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        )
    if not _created('leave_requests'):
        op.create_table(
            'leave_requests',
            sa.Column('id', sa.String(36), primary_key=True),
            sa.Column('staff_id', sa.String(36), sa.ForeignKey('staff.id', ondelete='CASCADE'), nullable=False),
            sa.Column('leave_type_id', sa.Integer(), sa.ForeignKey('leave_types.id'), nullable=False),
            sa.Column('start_date', sa.Date(), nullable=False),
            sa.Column('end_date', sa.Date(), nullable=False),
            sa.Column('half_day', sa.Boolean(), nullable=False),
            sa.Column('days', sa.Numeric(5, 1), nullable=False),
            sa.Column('reason', sa.String(255), nullable=True),
            sa.Column('status', sa.String(20), nullable=False),
            sa.Column('hr_note', sa.String(255), nullable=True),
            sa.Column('created_by_account_id', sa.String(36), sa.ForeignKey('auth_accounts.id', ondelete='SET NULL'), nullable=True),
            sa.Column('decided_by_account_id', sa.String(36), sa.ForeignKey('auth_accounts.id', ondelete='SET NULL'), nullable=True),
            sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
            *_stamp(),
        )
        op.create_index('ix_leave_requests_staff_dates', 'leave_requests', ['staff_id', 'start_date'])
        op.create_index('ix_leave_requests_status', 'leave_requests', ['status'])
    if not _created('holidays'):
        op.create_table(
            'holidays',
            sa.Column('id', sa.String(36), primary_key=True),
            sa.Column('day', sa.Date(), nullable=False, unique=True),
            sa.Column('name', sa.String(80), nullable=False),
            *_stamp(),
        )
    if not _created('attendance_days'):
        op.create_table(
            'attendance_days',
            sa.Column('id', sa.String(36), primary_key=True),
            sa.Column('staff_id', sa.String(36), sa.ForeignKey('staff.id', ondelete='CASCADE'), nullable=False),
            sa.Column('day', sa.Date(), nullable=False),
            sa.Column('check_in_at', sa.DateTime(timezone=True), nullable=True),
            sa.Column('check_out_at', sa.DateTime(timezone=True), nullable=True),
            sa.Column('status', sa.String(10), nullable=True),
            sa.Column('note', sa.String(255), nullable=True),
            sa.Column('updated_by_account_id', sa.String(36), sa.ForeignKey('auth_accounts.id', ondelete='SET NULL'), nullable=True),
            sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint('staff_id', 'day', name='uq_attendance_staff_day'),
        )
        op.create_index('ix_attendance_day', 'attendance_days', ['day'])
    if not _created('hr_documents'):
        op.create_table(
            'hr_documents',
            sa.Column('id', sa.String(36), primary_key=True),
            sa.Column('document_number', sa.String(20), nullable=False, unique=True),
            sa.Column('kind', sa.String(20), nullable=False),
            sa.Column('staff_id', sa.String(36), sa.ForeignKey('staff.id', ondelete='SET NULL'), nullable=True),
            sa.Column('period', sa.String(7), nullable=True),
            sa.Column('status', sa.String(20), nullable=False),
            sa.Column('offer_status', sa.String(20), nullable=True),
            sa.Column('data', sa.JSON(), nullable=False),
            sa.Column('rendered_html', sa.Text().with_variant(mysql.MEDIUMTEXT(), 'mysql'), nullable=True),
            sa.Column('net_amount', sa.Numeric(12, 2), nullable=True),
            sa.Column('created_by_account_id', sa.String(36), sa.ForeignKey('auth_accounts.id', ondelete='SET NULL'), nullable=True),
            sa.Column('finalised_by_account_id', sa.String(36), sa.ForeignKey('auth_accounts.id', ondelete='SET NULL'), nullable=True),
            sa.Column('finalised_at', sa.DateTime(timezone=True), nullable=True),
            sa.Column('emailed_at', sa.DateTime(timezone=True), nullable=True),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
            sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index('ix_hr_documents_kind_period', 'hr_documents', ['kind', 'period'])
        op.create_index('ix_hr_documents_staff', 'hr_documents', ['staff_id'])


def downgrade():
    for name in ('hr_documents', 'attendance_days', 'holidays', 'leave_requests', 'leave_types'):
        op.drop_table(name)
