"""Messaging: internal chat between staff, admins and HR, and client conversations.

Every conversation is one ChatThread. ``kind`` decides who may join and what it points at:

- DIRECT: two internal members. ``direct_key`` gives each pair one general thread, plus one
  thread per filing they discuss.
- GROUP: a named group of internal members, optionally about one filing.
- CASE: the client and the case team on one filing. One per case (enforced in the service
  layer; a partial unique index is added on PostgreSQL / SQLite only, as MySQL has none).
- QUERY: a question a client raised, optionally about one of their filings.

``case_id`` is set only when the conversation is about a filing. Who may read or post is
checked in the chat service, not here. Nothing is hard-deleted: old threads get ``hidden_at``
(see ``hide_after``) and stay in the tables for audit.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return str(uuid.uuid4())


# MySQL DATETIME keeps whole seconds by default; message order and unread counts need
# sub-second precision. PostgreSQL timestamps already keep microseconds.
PreciseDateTime = DateTime(timezone=True).with_variant(mysql.DATETIME(fsp=6), "mysql")


THREAD_KINDS = ("DIRECT", "GROUP", "CASE", "QUERY")
THREAD_STATUSES = ("open", "resolved", "closed")
MEMBER_ROLES = ("owner", "member")
ADDED_REASONS = ("member", "client", "case_assignment", "admin_joined", "query_owner")
MESSAGE_KINDS = ("text", "system")
MESSAGE_VISIBILITIES = ("all", "internal")  # internal = staff note, never shown to the client


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN (" + ", ".join(f"'{v}'" for v in values) + ")"


def direct_key_for(account_a: str, account_b: str, case_id: str | None = None) -> str:
    """``direct_key`` for a DIRECT thread: the two account ids sorted, plus the case id when
    the conversation is about a filing."""
    key = ":".join(sorted((account_a, account_b)))
    return f"{key}:{case_id}" if case_id else key


class ChatThread(Base):
    __tablename__ = "chat_threads"
    __table_args__ = (
        CheckConstraint(_in("kind", THREAD_KINDS), name="ck_ct_kind"),
        CheckConstraint(_in("status", THREAD_STATUSES), name="ck_ct_status"),
        CheckConstraint(
            "(kind = 'DIRECT' AND direct_key IS NOT NULL AND topic_id IS NULL)"
            " OR (kind = 'GROUP' AND name IS NOT NULL AND topic_id IS NULL AND direct_key IS NULL)"
            " OR (kind = 'CASE' AND case_id IS NOT NULL AND direct_key IS NULL AND topic_id IS NULL)"
            " OR (kind = 'QUERY' AND topic_id IS NOT NULL AND subject IS NOT NULL AND direct_key IS NULL)",
            name="ck_ct_shape",
        ),
        Index("ix_chat_threads_case", "case_id", "kind"),
        Index("ix_chat_threads_queue", "kind", "status", "owner_account_id"),  # unclaimed / my queries
        Index("ix_chat_threads_hide", "hidden_at", "hide_after"),  # daily hiding job
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    thread_number: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)  # MSG-0000001
    kind: Mapped[str] = mapped_column(String(10), nullable=False)
    name: Mapped[str | None] = mapped_column(String(100), nullable=True)  # GROUP only
    case_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("tax_filings.id", ondelete="CASCADE"), nullable=True)  # set only when about a filing
    topic_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("chat_query_topics.id"), nullable=True)  # QUERY only
    subject: Mapped[str | None] = mapped_column(String(150), nullable=True)  # QUERY only
    direct_key: Mapped[str | None] = mapped_column(String(110), unique=True, nullable=True)  # DIRECT only, see direct_key_for()
    status: Mapped[str] = mapped_column(String(10), default="open", nullable=False)
    owner_account_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("auth_accounts.id", ondelete="SET NULL"), nullable=True)  # QUERY: None = unclaimed
    created_by_account_id: Mapped[str] = mapped_column(String(36), ForeignKey("auth_accounts.id"), nullable=False)
    last_message_at: Mapped[datetime | None] = mapped_column(PreciseDateTime, nullable=True)
    last_message_preview: Mapped[str | None] = mapped_column(String(160), nullable=True)  # last client-visible message
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_by_account_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("auth_accounts.id", ondelete="SET NULL"), nullable=True)
    hide_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)  # None = stays visible
    hidden_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)  # set by the hiding job; kept for audit
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now, nullable=False)

    case: Mapped["app.platform.models.tax_filing.TaxFiling | None"] = relationship("TaxFiling")
    topic: Mapped["app.platform.models.lookups.ChatQueryTopic | None"] = relationship("ChatQueryTopic")
    participants: Mapped[list["ChatParticipant"]] = relationship(
        "ChatParticipant", back_populates="thread", cascade="all, delete-orphan"
    )
    messages: Mapped[list["ChatMessage"]] = relationship(
        "ChatMessage", back_populates="thread", cascade="all, delete-orphan", order_by="ChatMessage.created_at"
    )


class ChatParticipant(Base):
    """One account in one thread. ``left_at`` set = no longer a member (their messages stay)."""
    __tablename__ = "chat_participants"
    __table_args__ = (
        UniqueConstraint("thread_id", "account_id", name="uq_cp_thread_account"),
        CheckConstraint(_in("member_role", MEMBER_ROLES), name="ck_cp_role"),
        CheckConstraint(_in("added_reason", ADDED_REASONS), name="ck_cp_reason"),
        Index("ix_chat_participants_inbox", "account_id", "left_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    thread_id: Mapped[str] = mapped_column(String(36), ForeignKey("chat_threads.id", ondelete="CASCADE"), nullable=False)
    account_id: Mapped[str] = mapped_column(String(36), ForeignKey("auth_accounts.id"), nullable=False)
    member_role: Mapped[str] = mapped_column(String(10), default="member", nullable=False)  # GROUP owners manage members
    added_reason: Mapped[str] = mapped_column(String(20), default="member", nullable=False)
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    left_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_read_at: Mapped[datetime | None] = mapped_column(PreciseDateTime, nullable=True)  # unread = messages after this
    is_muted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    thread: Mapped[ChatThread] = relationship("ChatThread", back_populates="participants")
    account: Mapped["app.platform.models.auth.AuthAccount"] = relationship("AuthAccount")


class ChatMessage(Base):
    """A message in a thread. Deletes are soft (``deleted_at``); the text stays for audit."""
    __tablename__ = "chat_messages"
    __table_args__ = (
        CheckConstraint(_in("kind", MESSAGE_KINDS), name="ck_cm_kind"),
        CheckConstraint(_in("visibility", MESSAGE_VISIBILITIES), name="ck_cm_visibility"),
        Index("ix_chat_messages_thread", "thread_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    thread_id: Mapped[str] = mapped_column(String(36), ForeignKey("chat_threads.id", ondelete="CASCADE"), nullable=False)
    sender_account_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("auth_accounts.id"), nullable=True)  # None for system messages
    sender_type: Mapped[str | None] = mapped_column(String(30), nullable=True)  # snapshot of account_types.code
    sender_name: Mapped[str | None] = mapped_column(String(80), nullable=True)  # snapshot at time of writing
    kind: Mapped[str] = mapped_column(String(10), default="text", nullable=False)
    visibility: Mapped[str] = mapped_column(String(10), default="all", nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    reply_to_message_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("chat_messages.id"), nullable=True)
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_by_account_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("auth_accounts.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(PreciseDateTime, default=_now, nullable=False)

    thread: Mapped[ChatThread] = relationship("ChatThread", back_populates="messages")
    attachments: Mapped[list["ChatAttachment"]] = relationship(
        "ChatAttachment", back_populates="message", cascade="all, delete-orphan"
    )


class ChatAttachment(Base):
    """A file sent in a message. Same file columns as filing_documents so upload code is shared.
    "Save to case documents" copies the file into a filing_documents row and links it here."""
    __tablename__ = "chat_attachments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    message_id: Mapped[str] = mapped_column(String(36), ForeignKey("chat_messages.id", ondelete="CASCADE"), index=True, nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    stored_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    file_path: Mapped[str] = mapped_column(String(500), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    filing_document_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("filing_documents.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)

    message: Mapped[ChatMessage] = relationship("ChatMessage", back_populates="attachments")
