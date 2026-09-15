from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:
    from app.models.tax_filing import TaxFiling


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return str(uuid.uuid4())


class FilingDocument(Base):
    """Stores metadata and file path details for documents uploaded to tax filings."""
    __tablename__ = "filing_documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    filing_id: Mapped[str] = mapped_column(String(36), ForeignKey("tax_filings.id", ondelete="CASCADE"), index=True, nullable=False)
    document_group_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False, default=_uuid)
    
    category: Mapped[str] = mapped_column(String(50), nullable=False)  # e.g., Personal, Income, Deductions
    document_name: Mapped[str] = mapped_column(String(120), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    stored_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    file_path: Mapped[str] = mapped_column(String(500), nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    is_latest: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="pending_review", nullable=False)  # pending_review, accepted, rejected_reupload_requested, superseded
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    
    uploaded_by_type: Mapped[str] = mapped_column(String(20), default="client", nullable=False)  # client / staff
    uploaded_by_id: Mapped[str] = mapped_column(String(36), nullable=False)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reviewed_by_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

    filing: Mapped[TaxFiling] = relationship("TaxFiling", back_populates="documents")
