from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import Date, DateTime, ForeignKey, Index, Integer, JSON, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return str(uuid.uuid4())


class TaxFiling(Base):
    """Stores tax filing forms and all associated section data."""
    __tablename__ = "tax_filings"
    __table_args__ = (
        Index("ix_tax_filings_client_year", "client_id", "tax_year", "created_at"),  # client dashboard
        Index("ix_tax_filings_status_created", "status", "created_at"),  # stage counts / filter
        Index("ix_tax_filings_year_status", "tax_year", "status"),  # staff list by year
        Index("ix_tax_filings_created", "created_at"),  # newest-first lists, feed
        Index("ix_tax_filings_updated", "updated_at"),  # admin "recently updated"
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    client_id: Mapped[str] = mapped_column(String(36), ForeignKey("clients.id", ondelete="CASCADE"), index=True, nullable=False)
    
    # 01 - Basic Information
    tax_year: Mapped[int] = mapped_column(Integer, nullable=False)
    filing_type: Mapped[str] = mapped_column(String(50), nullable=False)
    first_name: Mapped[str] = mapped_column(String(50), nullable=False)
    last_name: Mapped[str] = mapped_column(String(50), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(25), nullable=True)

    # 02 - Personal & Filing Details
    date_of_birth: Mapped[date | None] = mapped_column(Date, nullable=True)
    country_of_citizenship: Mapped[str | None] = mapped_column(String(80), nullable=True)
    current_country_of_residence: Mapped[str | None] = mapped_column(String(80), nullable=True)
    us_tax_residency_status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    filing_status: Mapped[str | None] = mapped_column(String(50), nullable=True)
    filed_us_taxes_before: Mapped[str | None] = mapped_column(String(10), nullable=True)
    previous_tax_year_filed: Mapped[int | None] = mapped_column(Integer, nullable=True)

    address_line_1: Mapped[str | None] = mapped_column(String(120), nullable=True)
    address_line_2: Mapped[str | None] = mapped_column(String(120), nullable=True)
    city: Mapped[str | None] = mapped_column(String(80), nullable=True)
    state_province: Mapped[str | None] = mapped_column(String(80), nullable=True)
    postal_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    country: Mapped[str | None] = mapped_column(String(80), nullable=True)

    # 03 - Spouse Information
    spouse_first_name: Mapped[str | None] = mapped_column(String(50), nullable=True)
    spouse_last_name: Mapped[str | None] = mapped_column(String(50), nullable=True)
    spouse_date_of_birth: Mapped[date | None] = mapped_column(Date, nullable=True)
    spouse_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    spouse_phone: Mapped[str | None] = mapped_column(String(25), nullable=True)
    spouse_country_of_citizenship: Mapped[str | None] = mapped_column(String(80), nullable=True)
    spouse_us_tax_residency_status: Mapped[str | None] = mapped_column(String(50), nullable=True)

    # 04, 05, 06, 08 - Dynamic Sections (JSON)
    dependents: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list, nullable=False)
    income_categories: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    deductions_credits: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    special_situations: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)

    # 07 - Previous Tax Information
    has_previous_return_copy: Mapped[str | None] = mapped_column(String(10), nullable=True)
    received_irs_notice: Mapped[str | None] = mapped_column(String(10), nullable=True)
    unresolved_tax_issues: Mapped[str | None] = mapped_column(String(10), nullable=True)
    is_amended_return: Mapped[str | None] = mapped_column(String(10), nullable=True)

    # Meta
    case_number: Mapped[str] = mapped_column(String(30), unique=True, index=True, nullable=False)  # e.g., FLI_0000001; filled in by app.platform.services.numbering
    status: Mapped[str] = mapped_column(String(50), default="pending_review", nullable=False)

    def get_case_id(self, db: Session | None = None) -> str:
        """Returns readable FLI_xxxxxxx sequence ID, fallback to FLI_ prefix using UUID prefix or DB count."""
        if self.case_number:
            return self.case_number
        if db:
            count = db.query(TaxFiling).filter(TaxFiling.created_at <= self.created_at).count()
            return f"FLI_{count:07d}"
        return f"FLI_{self.id[:7].upper()}"
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now, nullable=False)

    client: Mapped["app.modules.client.models.client.Client"] = relationship("Client", foreign_keys=[client_id])
    documents: Mapped[list["app.platform.models.filing_document.FilingDocument"]] = relationship("FilingDocument", back_populates="filing", cascade="all, delete-orphan")
