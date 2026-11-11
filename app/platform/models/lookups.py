"""Admin-managed lookup tables (Final-Data-Model.md §1).

Adding a role, document type, tax year, tax type, stage, chat topic or account kind is one
row here, with no code change. Code compares on ``code``, never on ``id`` or
``name``. Rows are retired with ``is_active = False``, never deleted.

``AccountTypeLookup`` and ``StaffRoleLookup`` carry a suffix because the
``AccountType`` and ``StaffRole`` enums still exist; they move onto these tables
in later migration steps.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from sqlalchemy import Boolean, CheckConstraint, Date, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return str(uuid.uuid4())


CLIENT_STATUSES = ("Submitted", "In Progress", "Completed", "Closed", "Amendment")


class _LookupColumns:
    """Columns every lookup shares, so one admin settings screen can manage them all."""

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now, nullable=False)


class AccountTypeLookup(_LookupColumns, Base):
    """Kinds of login account (CLIENT, STAFF, ADMIN, HR, ...)."""
    __tablename__ = "account_types"

    code: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    number_prefix: Mapped[str] = mapped_column(String(10), unique=True, nullable=False)  # e.g. CLT -> CLT-00000001
    portal_path: Mapped[str] = mapped_column(String(80), nullable=False)  # e.g. /staff
    has_case_access: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_internal: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)  # firm side (STAFF, ADMIN, HR); may use internal chat


class StaffRoleLookup(_LookupColumns, Base):
    """Roles a staff member can act in on a case (INITIATOR, PREPARER, ...)."""
    __tablename__ = "staff_roles"

    code: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    is_case_role: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class TaxYear(_LookupColumns, Base):
    """Tax years clients can file for. ``year`` plays the role of ``code``."""
    __tablename__ = "tax_years"

    year: Mapped[int] = mapped_column(Integer, unique=True, nullable=False)
    is_open: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)  # shown in the year picker
    is_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    filing_deadline: Mapped[date | None] = mapped_column(Date, nullable=True)


class TaxType(_LookupColumns, Base):
    """Kinds of return (INDIVIDUAL, ...). Will replace tax_filings.filing_type."""
    __tablename__ = "tax_types"

    code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)


class DocumentCategory(_LookupColumns, Base):
    """Groups shown on the documents pages (Personal, Income, ...). ``code`` is what
    filing_documents.category and document_types.category store."""
    __tablename__ = "document_categories"

    code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)


class DocumentType(_LookupColumns, Base):
    """Document kinds for both case uploads (CASE) and HR staff files (STAFF)."""
    __tablename__ = "document_types"
    __table_args__ = (
        CheckConstraint("applies_to IN ('CASE', 'STAFF')", name="ck_document_types_applies_to"),
    )

    code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    category: Mapped[str] = mapped_column(String(50), ForeignKey("document_categories.code"), nullable=False)
    applies_to: Mapped[str] = mapped_column(String(10), default="CASE", nullable=False)
    allowed_mime_types: Mapped[str | None] = mapped_column(String(255), nullable=True)  # comma-separated; None = app default
    max_file_mb: Mapped[int | None] = mapped_column(Integer, nullable=True)


class CaseStage(_LookupColumns, Base):
    """Internal pipeline stages. ``client_status`` is what the client portal shows."""
    __tablename__ = "case_stages"
    __table_args__ = (
        CheckConstraint(
            "client_status IN (" + ", ".join(f"'{s}'" for s in CLIENT_STATUSES) + ")",
            name="ck_case_stages_client_status",
        ),
    )

    code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    client_status: Mapped[str] = mapped_column(String(20), nullable=False)
    is_terminal: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class ChatQueryTopic(_LookupColumns, Base):
    """Topics a client picks when asking a question (FILING_STATUS, REFUND, ...)."""
    __tablename__ = "chat_query_topics"

    code: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)


class RequiredDocument(Base):
    """Which document types a tax year + tax type needs."""
    __tablename__ = "required_documents"
    __table_args__ = (
        UniqueConstraint("tax_year_id", "tax_type_id", "document_type_id", name="uq_required_documents"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    tax_year_id: Mapped[int] = mapped_column(Integer, ForeignKey("tax_years.id"), nullable=False)
    tax_type_id: Mapped[int] = mapped_column(Integer, ForeignKey("tax_types.id"), nullable=False)
    document_type_id: Mapped[int] = mapped_column(Integer, ForeignKey("document_types.id"), nullable=False)
    is_required: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
