from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, Enum, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return str(uuid.uuid4())


class StaffRole(str, enum.Enum):
    INITIATOR = "INITIATOR"
    PREPARER = "PREPARER"
    REVIEWER = "REVIEWER"
    MANAGER = "MANAGER"
    HR = "HR"
    ADMIN = "ADMIN"  # Reserved; can be removed later if not needed


class Staff(Base):
    """Business entity representing employee details."""
    __tablename__ = "staff"
    __table_args__ = (Index("ix_staff_created", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    staff_number: Mapped[str] = mapped_column(String(20), unique=True, index=True, nullable=False)  # e.g. STF-00000001
    account_id: Mapped[str] = mapped_column(String(36), ForeignKey("auth_accounts.id", ondelete="CASCADE"), unique=True, nullable=False)
    first_name: Mapped[str] = mapped_column(String(15), nullable=False)
    last_name: Mapped[str | None] = mapped_column(String(15), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    job_title: Mapped[str | None] = mapped_column(String(50), nullable=True)  # e.g., Senior Tax Preparer
    role: Mapped[StaffRole] = mapped_column(Enum(StaffRole), nullable=False, default=StaffRole.INITIATOR)
    profile_picture: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Anything in the notifications feed newer than this counts as unread
    notifications_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now, nullable=False)

    account: Mapped["app.models.auth.AuthAccount"] = relationship("AuthAccount", foreign_keys=[account_id])
