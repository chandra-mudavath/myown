"""HR records: leave types and requests, holidays, daily attendance, and the documents HR generates
(payslips, offer letters and other letters).

Attendance stores only what someone did: a staff check-in, or an HR correction. Week offs,
holidays and approved leave are worked out when a month is shown, so they never go stale.
A generated document keeps its data (JSON) while it is a draft; finalising stores the rendered
HTML too, so a final document never changes even if its template does.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import JSON, Boolean, Date, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.platform.models.lookups import _LookupColumns


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return str(uuid.uuid4())


class LeaveType(_LookupColumns, Base):
    """Casual, sick, earned, unpaid, ... and how many days a year each gives."""
    __tablename__ = "leave_types"

    code: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    days_per_year: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_paid: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    carry_over: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)  # unused days move to next year
    max_carry_over: Mapped[int | None] = mapped_column(Integer, nullable=True)  # None = no limit


class LeaveRequest(Base):
    __tablename__ = "leave_requests"
    __table_args__ = (Index("ix_leave_requests_staff_dates", "staff_id", "start_date"),
                      Index("ix_leave_requests_status", "status"))

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    staff_id: Mapped[str] = mapped_column(String(36), ForeignKey("staff.id", ondelete="CASCADE"), nullable=False)
    leave_type_id: Mapped[int] = mapped_column(Integer, ForeignKey("leave_types.id"), nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    half_day: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)  # only for one-day requests
    days: Mapped[Decimal] = mapped_column(Numeric(5, 1), nullable=False)  # working days, week offs and holidays left out
    reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)  # pending|approved|rejected|cancelled
    hr_note: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_by_account_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("auth_accounts.id", ondelete="SET NULL"), nullable=True)
    decided_by_account_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("auth_accounts.id", ondelete="SET NULL"), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)

    staff: Mapped["app.modules.staff.models.staff.Staff"] = relationship("Staff")
    leave_type: Mapped[LeaveType] = relationship(LeaveType)


class Holiday(Base):
    __tablename__ = "holidays"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    day: Mapped[date] = mapped_column(Date, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)


class AttendanceDay(Base):
    """One staff member's day: their own check-in/out, or HR's correction (which wins)."""
    __tablename__ = "attendance_days"
    __table_args__ = (UniqueConstraint("staff_id", "day", name="uq_attendance_staff_day"),
                      Index("ix_attendance_day", "day"))

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    staff_id: Mapped[str] = mapped_column(String(36), ForeignKey("staff.id", ondelete="CASCADE"), nullable=False)
    day: Mapped[date] = mapped_column(Date, nullable=False)
    check_in_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    check_out_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str | None] = mapped_column(String(10), nullable=True)  # HR correction: P, A, HALF, L; None = from check-in
    note: Mapped[str | None] = mapped_column(String(255), nullable=True)
    updated_by_account_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("auth_accounts.id", ondelete="SET NULL"), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now, nullable=False)


class HrDocument(Base):
    """A document made from one of HR's fixed templates: payslip, offer letter, appointment letter, ..."""
    __tablename__ = "hr_documents"
    __table_args__ = (Index("ix_hr_documents_kind_period", "kind", "period"),
                      Index("ix_hr_documents_staff", "staff_id"))

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    document_number: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)  # OL-000001, PS-000001, ...
    kind: Mapped[str] = mapped_column(String(20), nullable=False)  # OFFER, APPOINTMENT, PAYSLIP, SALARY_REVISION, ...
    staff_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("staff.id", ondelete="SET NULL"), nullable=True)
    period: Mapped[str | None] = mapped_column(String(7), nullable=True)  # payslips: YYYY-MM
    status: Mapped[str] = mapped_column(String(20), default="draft", nullable=False)  # draft|final|void
    offer_status: Mapped[str | None] = mapped_column(String(20), nullable=True)  # offers: sent|accepted|declined|withdrawn
    data: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    rendered_html: Mapped[str | None] = mapped_column(Text().with_variant(mysql.MEDIUMTEXT(), "mysql"), nullable=True)  # set at Finalise
    net_amount: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)  # payslips
    created_by_account_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("auth_accounts.id", ondelete="SET NULL"), nullable=True)
    finalised_by_account_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("auth_accounts.id", ondelete="SET NULL"), nullable=True)
    finalised_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    emailed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now, nullable=False)

    staff: Mapped["app.modules.staff.models.staff.Staff | None"] = relationship("Staff")
