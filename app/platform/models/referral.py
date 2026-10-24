"""Referrals: colleagues a client refers (by form or by signing up with their link), and the tax
team's follow-up calls on each one."""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from sqlalchemy import Date, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return str(uuid.uuid4())


class ReferralLead(Base):
    """One referred person. status: new, contacted, follow_up, interested, not_interested, signed_up, filed."""
    __tablename__ = "referral_leads"
    __table_args__ = (
        Index("ix_referral_leads_status", "status", "created_at"),
        Index("ix_referral_leads_staff", "assigned_staff_id", "status"),
        Index("ix_referral_leads_referrer", "referrer_client_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    referrer_client_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("clients.id", ondelete="SET NULL"), nullable=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(30), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    relationship: Mapped[str | None] = mapped_column(String(60), nullable=True)  # colleague, friend, family…
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(10), default="form", nullable=False)  # form (client), staff, admin, link (signed up)
    status: Mapped[str] = mapped_column(String(20), default="new", nullable=False)
    assigned_staff_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("staff.id", ondelete="SET NULL"), nullable=True)
    next_follow_up: Mapped[date | None] = mapped_column(Date, nullable=True)
    last_contacted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    signed_up_client_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("clients.id", ondelete="SET NULL"), nullable=True)
    created_by_account_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now, nullable=False)


class ReferralFollowUp(Base):
    """A call or note logged on a referral lead."""
    __tablename__ = "referral_followups"
    __table_args__ = (Index("ix_referral_followups_lead", "lead_id", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    lead_id: Mapped[str] = mapped_column(String(36), ForeignKey("referral_leads.id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[str] = mapped_column(String(10), default="call", nullable=False)  # call / note / status / assign
    outcome: Mapped[str | None] = mapped_column(String(30), nullable=True)  # answered, no_answer, busy, wrong_number, left_message
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    author_account_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    author_name: Mapped[str] = mapped_column(String(80), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
