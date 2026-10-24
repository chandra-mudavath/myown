"""What the client fills in for a tax year outside the old New filing form (client feedback, Oct 2026):
the Tax Notes answers, refund bank details, residency, dependents, FBAR and e-file authorization, saved per filing
as one encrypted JSON document per section."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.crypto import EncryptedJSON
from app.core.database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return str(uuid.uuid4())


class FilingIntake(Base):
    """section: "tax_notes" or "bank". data holds the form's fields as saved."""
    __tablename__ = "filing_intake"
    __table_args__ = (UniqueConstraint("filing_id", "section", name="uq_filing_intake_section"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    filing_id: Mapped[str] = mapped_column(String(36), ForeignKey("tax_filings.id", ondelete="CASCADE"), index=True, nullable=False)
    section: Mapped[str] = mapped_column(String(20), nullable=False)
    # Encrypted at rest (bank / FBAR numbers, SSN/TIN, e-file PINs); plain dict in Python. See app/core/crypto.py.
    data: Mapped[dict[str, Any]] = mapped_column(EncryptedJSON, default=dict, nullable=False)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)  # client confirmed it's complete
    updated_by_account_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now, nullable=False)
