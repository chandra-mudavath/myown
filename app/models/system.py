"""Audit trail and stored notifications. entity_type + entity_id can point at any record."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return str(uuid.uuid4())


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_entity", "entity_type", "entity_id"),
        Index("ix_audit_logs_actor", "actor_account_id", "created_at"),
        Index("ix_audit_logs_created", "created_at"),  # admin audit view by date
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    actor_account_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("auth_accounts.id", ondelete="SET NULL"), nullable=True)  # None for system jobs
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)  # tax_filing, filing_document, invoice, ...
    entity_id: Mapped[str] = mapped_column(String(36), nullable=False)
    old_values: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    new_values: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)


class Notification(Base):
    """A stored notification for one account.

    The staff notifications page currently builds its feed from case data and
    staff.notifications_seen_at; this table is for notifications that need to be stored.
    """
    __tablename__ = "notifications"
    __table_args__ = (Index("ix_notifications_recipient", "recipient_account_id", "is_read", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    recipient_account_id: Mapped[str] = mapped_column(String(36), ForeignKey("auth_accounts.id", ondelete="CASCADE"), nullable=False)
    type: Mapped[str] = mapped_column(String(50), nullable=False)
    title: Mapped[str] = mapped_column(String(150), nullable=False)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    entity_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    entity_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
