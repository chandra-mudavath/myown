"""Service catalog, what each case is charged, invoices and payments."""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import Boolean, CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return str(uuid.uuid4())


class Service(Base):
    """A billable service with its list price."""
    __tablename__ = "services"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    service_code: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    base_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now, nullable=False)


class CaseService(Base):
    """A service charged on a case. unit_price is copied from the catalog when added,
    so later price changes don't alter past invoices."""
    __tablename__ = "case_services"
    __table_args__ = (CheckConstraint("quantity > 0", name="ck_cs_quantity"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    case_id: Mapped[str] = mapped_column(String(36), ForeignKey("tax_filings.id", ondelete="CASCADE"), index=True, nullable=False)
    # The invoice that billed this line; a line belongs to one invoice so nothing is billed twice.
    invoice_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("invoices.id", ondelete="CASCADE"), index=True, nullable=True)
    service_id: Mapped[str] = mapped_column(String(36), ForeignKey("services.id"), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    total_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)

    service: Mapped["Service"] = relationship("Service")


class Invoice(Base):
    __tablename__ = "invoices"
    __table_args__ = (
        CheckConstraint("status IN ('draft', 'issued', 'paid', 'partially_paid', 'void')", name="ck_inv_status"),
        Index("ix_invoices_status_due", "status", "due_date"),  # unpaid / overdue
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    invoice_number: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    client_id: Mapped[str] = mapped_column(String(36), ForeignKey("clients.id"), index=True, nullable=False)
    case_id: Mapped[str] = mapped_column(String(36), ForeignKey("tax_filings.id"), index=True, nullable=False)
    subtotal: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    discount_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"), nullable=False)
    tax_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"), nullable=False)
    total_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="draft", nullable=False)
    invoice_date: Mapped[date] = mapped_column(Date, nullable=False)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)

    payments: Mapped[list["Payment"]] = relationship("Payment", back_populates="invoice")


class Payment(Base):
    __tablename__ = "payments"
    __table_args__ = (
        CheckConstraint("status IN ('pending', 'succeeded', 'failed', 'refunded')", name="ck_pay_status"),
        CheckConstraint("amount > 0", name="ck_pay_amount"),
        Index("ix_payments_status_paid", "status", "paid_at"),  # revenue by period
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    payment_number: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    invoice_id: Mapped[str] = mapped_column(String(36), ForeignKey("invoices.id"), index=True, nullable=False)
    client_id: Mapped[str] = mapped_column(String(36), ForeignKey("clients.id"), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    payment_method: Mapped[str] = mapped_column(String(30), nullable=False)
    transaction_reference: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, nullable=False)
    # How the payment arrived: "manual" (recorded by admin, or submitted by the client for checking)
    # or an online gateway later. A client submission stays "pending" until an admin verifies it.
    gateway: Mapped[str] = mapped_column(String(20), default="manual", nullable=False)
    gateway_payment_id: Mapped[str | None] = mapped_column(String(100), unique=True, nullable=True)
    submitted_by_account_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("auth_accounts.id", ondelete="SET NULL"), nullable=True)
    # What the client actually sent when it wasn't the invoice currency (e.g. INR over UPI).
    original_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    original_currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    # Optional screenshot / bank slip, kept in private storage (never under /storage).
    proof_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    proof_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    proof_mime: Mapped[str | None] = mapped_column(String(100), nullable=True)
    verified_by_account_id: Mapped[str | None] = mapped_column(String(36), ForeignKey("auth_accounts.id", ondelete="SET NULL"), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failure_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)  # why a submission was rejected

    invoice: Mapped["Invoice"] = relationship("Invoice", back_populates="payments")
