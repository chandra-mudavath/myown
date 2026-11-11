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


class Admin(Base):
    """Business entity representing system administrator details."""
    __tablename__ = "admins"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    admin_number: Mapped[str] = mapped_column(String(20), unique=True, index=True, nullable=False)  # e.g. ADM-00000001
    account_id: Mapped[str] = mapped_column(String(36), ForeignKey("auth_accounts.id", ondelete="CASCADE"), unique=True, nullable=False)
    first_name: Mapped[str] = mapped_column(String(15), nullable=False)
    last_name: Mapped[str | None] = mapped_column(String(15), nullable=True)
    profile_picture: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now, nullable=False)

    account: Mapped["app.models.auth.AuthAccount"] = relationship("AuthAccount", foreign_keys=[account_id])
