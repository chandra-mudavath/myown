from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class IdSequence(Base):
    """Counter behind each human-readable number (CLI-00000001, FLI_0000001, DOC-0000001, ...).

    The UUID primary key stays the real identifier everywhere; these numbers are what people
    see and quote. Numbers are handed out by app.services.numbering, never by counting rows,
    so a deleted record never causes its number to be reused.
    """
    __tablename__ = "id_sequences"

    name: Mapped[str] = mapped_column(String(40), primary_key=True)  # CLIENT, CASE, DOCUMENT, ACCOUNT_STAFF, ...
    prefix: Mapped[str] = mapped_column(String(10), nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False)  # zero-padded digits
    current_value: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now, nullable=False)
