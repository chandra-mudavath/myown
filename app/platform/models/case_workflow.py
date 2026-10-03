"""Who works on a case, how it moved through stages, and its IRS e-file trail."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return str(uuid.uuid4())


class CaseAssignment(Base):
    """A staff member acting in one role on one case. A case has at most one active row per role
    (enforced in the service layer; partial unique indexes aren't portable)."""
    __tablename__ = "case_assignments"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'ended')", name="ck_ca_status"),
        Index("ix_case_assignments_staff_status", "staff_id", "status"),  # "My Queue"
        Index("ix_case_assignments_case_status", "case_id", "status"),  # who is working a case
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    case_id: Mapped[str] = mapped_column(String(36), ForeignKey("tax_filings.id", ondelete="CASCADE"), index=True, nullable=False)
    staff_id: Mapped[str] = mapped_column(String(36), ForeignKey("staff.id"), nullable=False)
    role_id: Mapped[int] = mapped_column(Integer, ForeignKey("staff_roles.id"), nullable=False)
    assigned_by_account_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("auth_accounts.id", ondelete="SET NULL"), nullable=True)
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    unassigned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(10), default="active", nullable=False)

    case: Mapped["app.platform.models.tax_filing.TaxFiling"] = relationship("TaxFiling")
    staff: Mapped["app.modules.staff.models.staff.Staff"] = relationship("Staff")
    role: Mapped["app.platform.models.lookups.StaffRoleLookup"] = relationship("StaffRoleLookup")


class CaseStageHistory(Base):
    """One row per stage change, with who made it and why."""
    __tablename__ = "case_stage_history"
    __table_args__ = (Index("ix_case_stage_history_case_id", "case_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    case_id: Mapped[str] = mapped_column(String(36), ForeignKey("tax_filings.id", ondelete="CASCADE"), nullable=False)
    from_stage_code: Mapped[str | None] = mapped_column(String(50), ForeignKey("case_stages.code"), nullable=True)  # None for the first stage
    to_stage_code: Mapped[str] = mapped_column(String(50), ForeignKey("case_stages.code"), nullable=False)
    changed_by_account_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("auth_accounts.id", ondelete="SET NULL"), nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)


class IrsSubmission(Base):
    """An e-file submission for a Form 8879 case. Paper filings never get one."""
    __tablename__ = "irs_submissions"
    __table_args__ = (
        CheckConstraint("status IN ('pending', 'submitted', 'accepted', 'rejected')", name="ck_irs_sub_status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    submission_number: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    case_id: Mapped[str] = mapped_column(String(36), ForeignKey("tax_filings.id", ondelete="CASCADE"), index=True, nullable=False)
    submitted_by_account_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("auth_accounts.id", ondelete="SET NULL"), nullable=True)
    irs_submission_id: Mapped[str | None] = mapped_column(String(50), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now, nullable=False)

    case: Mapped["app.platform.models.tax_filing.TaxFiling"] = relationship("TaxFiling")
    acknowledgments: Mapped[list["Acknowledgment"]] = relationship(
        "Acknowledgment", back_populates="submission", cascade="all, delete-orphan"
    )


class Acknowledgment(Base):
    """The IRS response to a submission."""
    __tablename__ = "acknowledgments"
    __table_args__ = (CheckConstraint("status IN ('accepted', 'rejected')", name="ck_ack_status"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    ack_number: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    submission_id: Mapped[str] = mapped_column(String(36), ForeignKey("irs_submissions.id", ondelete="CASCADE"), index=True, nullable=False)
    recorded_by_account_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("auth_accounts.id", ondelete="SET NULL"), nullable=True)
    irs_ack_number: Mapped[str | None] = mapped_column(String(50), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)

    submission: Mapped["IrsSubmission"] = relationship("IrsSubmission", back_populates="acknowledgments")
