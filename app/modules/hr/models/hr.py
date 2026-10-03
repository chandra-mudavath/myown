from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return str(uuid.uuid4())


class HR(Base):
    """HR user. Manages staff employment records; has no access to tax cases.

    HR logins need the HR account type, which arrives when AccountType moves onto the
    account_types table; until then no rows are created here.
    """
    __tablename__ = "hr"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    hr_number: Mapped[str] = mapped_column(String(20), unique=True, index=True, nullable=False)  # e.g. HR-00000001
    account_id: Mapped[str] = mapped_column(String(36), ForeignKey("auth_accounts.id", ondelete="CASCADE"), unique=True, nullable=False)
    department: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now, nullable=False)

    account: Mapped["app.platform.models.auth.AuthAccount"] = relationship("AuthAccount", foreign_keys=[account_id])
