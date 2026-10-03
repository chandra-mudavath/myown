"""Staff roles and the HR-managed employment records (Final-Data-Model.md §3, STAFF)."""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return str(uuid.uuid4())


class StaffRoleAssignment(Base):
    """Roles a staff member may act in. Will replace the single staff.role enum column."""
    __tablename__ = "staff_role_assignments"
    __table_args__ = (UniqueConstraint("staff_id", "role_id", name="uq_staff_role"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    staff_id: Mapped[str] = mapped_column(String(36), ForeignKey("staff.id", ondelete="CASCADE"), index=True, nullable=False)
    role_id: Mapped[int] = mapped_column(Integer, ForeignKey("staff_roles.id"), nullable=False)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    assigned_by_account_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("auth_accounts.id", ondelete="SET NULL"), nullable=True)
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)

    staff: Mapped["app.models.staff.Staff"] = relationship("Staff", foreign_keys=[staff_id])
    role: Mapped["app.models.lookups.StaffRoleLookup"] = relationship("StaffRoleLookup")


class StaffEmploymentDetails(Base):
    """One employment record per staff member, maintained by HR."""
    __tablename__ = "staff_employment_details"
    __table_args__ = (Index("ix_staff_employment_status", "employment_status"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    staff_id: Mapped[str] = mapped_column(String(36), ForeignKey("staff.id", ondelete="CASCADE"), unique=True, nullable=False)
    employee_number: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    date_of_joining: Mapped[date | None] = mapped_column(Date, nullable=True)
    employment_type: Mapped[str | None] = mapped_column(String(30), nullable=True)  # FULL_TIME, PART_TIME, CONTRACT, ...
    department: Mapped[str | None] = mapped_column(String(80), nullable=True)
    designation: Mapped[str | None] = mapped_column(String(80), nullable=True)
    reporting_manager_staff_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("staff.id", ondelete="SET NULL"), nullable=True)
    employment_status: Mapped[str] = mapped_column(String(30), default="ACTIVE", nullable=False)
    termination_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    current_salary: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    updated_by_account_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("auth_accounts.id", ondelete="SET NULL"), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now, nullable=False)

    staff: Mapped["app.models.staff.Staff"] = relationship("Staff", foreign_keys=[staff_id])
    reporting_manager: Mapped["app.models.staff.Staff | None"] = relationship("Staff", foreign_keys=[reporting_manager_staff_id])


class StaffDocument(Base):
    """HR job documents (offer letter, ID proof, contract). Same file columns as filing_documents."""
    __tablename__ = "staff_documents"
    __table_args__ = (Index("ix_staff_documents_expiry", "expiry_date"),)  # expiring soon

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    document_number: Mapped[str] = mapped_column(String(20), unique=True, index=True, nullable=False)  # e.g. SDOC-0000001
    staff_id: Mapped[str] = mapped_column(String(36), ForeignKey("staff.id", ondelete="CASCADE"), index=True, nullable=False)
    document_type_id: Mapped[int] = mapped_column(Integer, ForeignKey("document_types.id"), nullable=False)  # applies_to = STAFF
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    stored_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    file_path: Mapped[str] = mapped_column(String(500), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    uploaded_by_account_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("auth_accounts.id", ondelete="SET NULL"), nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    expiry_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    staff: Mapped["app.models.staff.Staff"] = relationship("Staff", foreign_keys=[staff_id])
    document_type: Mapped["app.models.lookups.DocumentType"] = relationship("DocumentType")


class StaffSalaryHistory(Base):
    """Every salary change, with who approved it."""
    __tablename__ = "staff_salary_history"
    __table_args__ = (Index("ix_staff_salary_history_staff_date", "staff_id", "effective_date"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    staff_id: Mapped[str] = mapped_column(String(36), ForeignKey("staff.id", ondelete="CASCADE"), index=True, nullable=False)
    effective_date: Mapped[date] = mapped_column(Date, nullable=False)
    previous_salary: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    new_salary: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    hike_percentage: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    approved_by_account_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("auth_accounts.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)

    staff: Mapped["app.models.staff.Staff"] = relationship("Staff", foreign_keys=[staff_id])
