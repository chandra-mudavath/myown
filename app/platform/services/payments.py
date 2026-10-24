"""Client payments shared by the client and admin portals.

Clients pay an issued invoice: the full balance by default, or a smaller amount they enter (the rest
stays due, and the invoice is partially paid). Two ways:

* PayPal (the main option; also takes cards and Venmo). We create a PayPal order and a *pending*
  payment, send the client to PayPal, and capture the order when they come back to our return URL.
  Only a completed capture makes the payment succeeded. There are no webhooks: a client who approves
  and never comes back is not charged (the order is never captured), and a capture PayPal leaves
  pending is checked by an admin in the PayPal dashboard and verified here like a manual payment.
* UPI or bank transfer: the client submits the reference here, which creates a *pending* payment.
  An admin verifies it (it becomes succeeded and counts towards the invoice) or rejects it
  (status failed, with the reason shown to the client).

When an invoice is paid in full and its case is waiting at payment_pending, the case moves on to
client_review so the client can see their return.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

import httpx
from fastapi import UploadFile
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.crypto import encrypt_bytes
from app.platform.models.billing import Invoice, Payment
from app.platform.models.system import AuditLog
from app.platform.models.tax_filing import TaxFiling
from app.platform.services import case_actions, paypal
from app.platform.services.catalog import case_stages
from app.platform.services.storage_service import generate_uuid, sanitize_filename

CENT = Decimal("0.01")
OPEN = ("issued", "partially_paid")
# What a client can pay with, and the currency they normally send. upi / bank_transfer are checked by an admin.
CLIENT_METHODS = {"paypal": ("PayPal", "USD"), "upi": ("UPI (India)", "INR"), "bank_transfer": ("Bank transfer / wire", "USD")}
MANUAL_METHODS = ("upi", "bank_transfer")
PAYPAL_CANCELLED = "Cancelled at PayPal."
PAYPAL_UNFINISHED = "PayPal checkout wasn't completed."
CURRENCIES = ("USD", "INR", "CAD", "GBP", "EUR", "AUD", "AED", "SGD")
PROOF_TYPES = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}
CLIENT_STATUS_LABELS = {"pending": "Awaiting verification", "succeeded": "Paid", "failed": "Not accepted", "refunded": "Refunded"}
PAID_STAGE_FROM, PAID_STAGE_TO = "payment_pending", "client_review"


def _money(value) -> Decimal:
    return Decimal(value or 0).quantize(CENT)


def _pay_amount(raw: str | None, due: Decimal) -> Decimal | str:
    """The USD amount the client chose to pay (blank means the full balance), or an error message."""
    if not (raw or "").strip():
        return due
    try:
        amount = Decimal(str(raw).replace(",", "").strip()).quantize(CENT)
    except InvalidOperation:
        return "Enter the amount to pay, e.g. 149.99."
    if amount <= 0:
        return "Enter the amount to pay."
    if amount > due:
        return f"That's more than the balance due (${due:,.2f})."
    return amount


def _audit(db: Session, actor_id: str | None, action: str, entity_id: str, old: dict | None, new: dict | None) -> None:
    db.add(AuditLog(actor_account_id=actor_id, action=action, entity_type="payment", entity_id=entity_id,
                    old_values=old, new_values=new))


def paid_amount(db: Session, invoice_id: str) -> Decimal:
    total = db.query(func.coalesce(func.sum(Payment.amount), 0)).filter(
        Payment.invoice_id == invoice_id, Payment.status == "succeeded").scalar()
    return _money(total)


def balance(db: Session, inv: Invoice) -> Decimal:
    return Decimal("0.00") if inv.status == "void" else _money(inv.total_amount) - paid_amount(db, inv.id)


def settle(db: Session, inv: Invoice) -> bool:
    """Set the invoice status from its succeeded payments. Returns True when it is now paid in full."""
    if inv.status == "void":
        return False
    paid = paid_amount(db, inv.id)
    if paid >= _money(inv.total_amount):
        inv.status = "paid"
    elif paid > 0:
        inv.status = "partially_paid"
    elif inv.status == "draft":
        pass
    else:
        inv.status = "issued"
    return inv.status == "paid"


def advance_case_if_paid(db: Session, inv: Invoice, actor_account_id: str | None) -> None:
    """Move the case from payment_pending to client_review once nothing on it is left to pay.

    Call after the payment is committed (move_stage commits)."""
    filing = db.get(TaxFiling, inv.case_id)
    if not filing or filing.status != PAID_STAGE_FROM:
        return
    if PAID_STAGE_TO not in {s.code for s in case_stages(db)}:
        return
    still_open = db.query(Invoice).filter(Invoice.case_id == filing.id, Invoice.status.in_(OPEN)).count()
    if not still_open:
        case_actions.move_stage(db, filing, PAID_STAGE_TO, actor_account_id)


# ── Client side ───────────────────────────────────────────────────────────────

def client_billing(db: Session, client_id: str, filing: TaxFiling | None) -> dict:
    """Open invoices with balances, and every payment, for one client's filing."""
    if not filing or filing.client_id != client_id:
        return {"open_invoices": [], "amount_due": Decimal("0.00"), "payments": [], "has_pending": False}
    invoices = (db.query(Invoice).filter(Invoice.case_id == filing.id, Invoice.client_id == client_id, Invoice.status.in_(OPEN))
                .order_by(Invoice.invoice_date, Invoice.created_at).all())
    open_invoices = [{"invoice": i, "balance": balance(db, i)} for i in invoices]
    payments = (db.query(Payment, Invoice).join(Invoice, Invoice.id == Payment.invoice_id)
                .filter(Invoice.case_id == filing.id, Payment.client_id == client_id)
                .order_by(Payment.created_at.desc()).all())
    # PayPal checkouts that never got as far as a capture aren't payments; leave them out of the history.
    payments = [(p, i) for p, i in payments if not (p.gateway == "paypal" and not p.transaction_reference)]
    pending_invoice_ids = {p.invoice_id for p, _ in payments if p.status == "pending"}
    for row in open_invoices:
        row["pending"] = row["invoice"].id in pending_invoice_ids
    return {
        "open_invoices": open_invoices,
        "amount_due": sum((r["balance"] for r in open_invoices), Decimal("0.00")),
        "payments": [{"payment": p, "invoice": i,
                      "status_label": "Processing at PayPal" if p.gateway == "paypal" and p.status == "pending"
                      else CLIENT_STATUS_LABELS.get(p.status, p.status),
                      "method_label": CLIENT_METHODS.get(p.payment_method, (p.payment_method.replace("_", " ").title(),))[0]}
                     for p, i in payments],
        "has_pending": bool(pending_invoice_ids),
    }


def _save_proof(file: UploadFile, inv: Invoice) -> tuple[str, str, str] | str:
    """Store the proof privately. Returns (path, original name, mime) or an error message."""
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in PROOF_TYPES:
        return "The proof must be a PDF, PNG or JPG file."
    data = file.file.read(settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024 + 1)
    if len(data) > settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024:
        return f"The proof file is larger than {settings.MAX_UPLOAD_SIZE_MB} MB."
    if not data:
        return "The proof file is empty."
    folder = Path(settings.PAYMENT_PROOFS_DIR) / inv.client_id
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{inv.invoice_number}_{generate_uuid()[:8]}{ext}"
    path.write_bytes(encrypt_bytes(data))  # encrypted at rest, like documents
    return str(path), sanitize_filename(file.filename or f"proof{ext}")[:255], PROOF_TYPES[ext]


def submit_manual(db: Session, account_id: str, client_id: str, invoice_id: str, method: str, reference: str,
                  sent_amount: str, sent_currency: str, proof: UploadFile | None, pay_amount: str = "") -> str | None:
    """A client says they paid an invoice by UPI or bank transfer. Returns an error message, or None."""
    inv = db.get(Invoice, invoice_id) if invoice_id else None
    if not inv or inv.client_id != client_id or inv.status not in OPEN:
        return "That invoice can't be paid right now. Refresh the page and try again."
    if method not in MANUAL_METHODS:
        return "Choose how you paid."
    ref = " ".join((reference or "").split())[:100]
    if len(ref) < 4:
        return "Enter the transaction / reference number from your payment."
    if db.query(Payment).filter(Payment.invoice_id == inv.id, Payment.status == "pending", Payment.gateway == "manual").first():
        return "You've already sent details for this invoice. We'll confirm it once it's verified."
    if db.query(Payment).filter(Payment.transaction_reference == ref, Payment.status.in_(("pending", "succeeded"))).first():
        return "That reference number has already been submitted."
    due = balance(db, inv)
    if due <= 0:
        return "Nothing is due on this invoice."
    amount = _pay_amount(pay_amount, due)
    if isinstance(amount, str):
        return amount

    currency = (sent_currency or "").upper()
    if currency not in CURRENCIES:
        return "Choose the currency you paid in."
    try:
        sent = Decimal(str(sent_amount or "").replace(",", "").strip()).quantize(CENT)
    except InvalidOperation:
        return "Enter the amount you paid, e.g. 12500 or 149.99."
    if sent <= 0 or sent > Decimal("100000000"):
        return "Enter the amount you paid."

    proof_path = proof_name = proof_mime = None
    if proof is not None and proof.filename:
        saved = _save_proof(proof, inv)
        if isinstance(saved, str):
            return saved
        proof_path, proof_name, proof_mime = saved

    # The payment counts the chosen amount in the invoice currency; what was actually sent is kept alongside.
    pay = Payment(invoice_id=inv.id, client_id=client_id, amount=amount, payment_method=method, transaction_reference=ref,
                  status="pending", gateway="manual", submitted_by_account_id=account_id,
                  original_amount=sent, original_currency=currency,
                  proof_path=proof_path, proof_filename=proof_name, proof_mime=proof_mime)
    db.add(pay)
    db.flush()
    _audit(db, account_id, "payment_submitted", pay.id, None,
           {"payment_number": pay.payment_number, "invoice": inv.invoice_number, "amount": str(amount), "method": method,
            "sent": f"{sent} {currency}", "reference": ref})
    db.commit()
    return None


# ── PayPal ────────────────────────────────────────────────────────────────────

def _locked_invoice(db: Session, invoice_id: str) -> Invoice | None:
    """The invoice row, locked until commit so two captures can't both pay the same balance."""
    return db.query(Invoice).filter(Invoice.id == invoice_id).with_for_update().first()


def start_paypal(db: Session, account_id: str, client_id: str, invoice_id: str, return_url: str,
                 cancel_url: str, pay_amount: str = "") -> tuple[str | None, str | None]:
    """Create a PayPal order for an invoice's balance (or the part the client chose). Returns (URL to send the client to, error)."""
    if not paypal.enabled():
        return None, "PayPal isn't available right now. Please use another payment method."
    inv = db.get(Invoice, invoice_id) if invoice_id else None
    if not inv or inv.client_id != client_id or inv.status not in OPEN:
        return None, "That invoice can't be paid right now. Refresh the page and try again."
    due = balance(db, inv)
    if due <= 0:
        return None, "Nothing is due on this invoice."
    amount = _pay_amount(pay_amount, due)
    if isinstance(amount, str):
        return None, amount
    # An earlier checkout the client walked away from is replaced (it can still finish if they approved it).
    for old in db.query(Payment).filter(Payment.invoice_id == inv.id, Payment.gateway == "paypal",
                                        Payment.status == "pending", Payment.transaction_reference.is_(None)):
        old.status, old.failure_reason = "failed", PAYPAL_UNFINISHED
    pay = Payment(invoice_id=inv.id, client_id=client_id, amount=amount, payment_method="paypal", status="pending",
                  gateway="paypal", submitted_by_account_id=account_id, original_amount=amount, original_currency="USD")
    db.add(pay)
    db.flush()
    try:
        order_id, link = paypal.create_order(
            amount=amount, currency="USD", reference=inv.invoice_number, payment_number=pay.payment_number, custom_id=pay.id,
            description=f"{settings.APP_NAME} invoice {inv.invoice_number}", return_url=return_url, cancel_url=cancel_url)
    except (paypal.PayPalError, httpx.HTTPError):
        db.rollback()
        return None, "We couldn't reach PayPal. Please try again in a few minutes."
    pay.gateway_payment_id = order_id
    _audit(db, account_id, "paypal_checkout_started", pay.id, None,
           {"payment_number": pay.payment_number, "invoice": inv.invoice_number, "amount": str(amount), "order": order_id})
    db.commit()
    return link, None


def _apply_capture(db: Session, pay: Payment, inv: Invoice, capture: dict) -> str | None:
    """Record what PayPal says happened to a capture. Commits. Returns a message for the client when it didn't succeed."""
    if capture.get("id"):
        pay.transaction_reference = capture["id"][:100]
    status = capture.get("status", "")
    if status == "COMPLETED":
        if pay.status != "succeeded":
            amount = capture.get("amount") or {}
            pay.original_amount = _money(amount.get("value") or pay.amount)
            pay.original_currency = (amount.get("currency_code") or "USD")[:3]
            pay.status, pay.paid_at, pay.failure_reason = "succeeded", datetime.now(timezone.utc), None
            db.flush()
            old = inv.status
            settle(db, inv)
            _audit(db, None, "paypal_payment_captured", pay.id, {"invoice_status": old},
                   {"capture": pay.transaction_reference, "amount": str(pay.original_amount), "invoice_status": inv.status})
            db.commit()
            advance_case_if_paid(db, inv, None)
        return None
    if status == "PENDING":  # e.g. an eCheck or a PayPal review; an admin verifies it once PayPal clears it
        db.commit()
        return None
    pay.status, pay.failure_reason = "failed", f"PayPal didn't complete the payment ({status.lower() or 'unknown'})."
    _audit(db, None, "paypal_payment_failed", pay.id, None, {"capture_status": status})
    db.commit()
    return "PayPal didn't complete the payment. You were not charged; please try again or use another method."


def finish_paypal(db: Session, order_id: str, client_id: str | None = None) -> str | None:
    """Capture an order the client approved. Safe to call twice (a refreshed return page). Returns an error or None."""
    pay = db.query(Payment).filter(Payment.gateway == "paypal", Payment.gateway_payment_id == order_id).first() if order_id else None
    if not pay or (client_id and pay.client_id != client_id):
        return "We couldn't find that PayPal payment."
    if pay.status in ("succeeded", "refunded") or pay.transaction_reference:
        return None  # already captured (or captured and waiting on PayPal)
    inv = _locked_invoice(db, pay.invoice_id)
    if inv.status not in OPEN or balance(db, inv) < _money(pay.amount):
        pay.status, pay.failure_reason = "failed", "This invoice no longer needed this payment."
        db.commit()
        return "This invoice no longer needs this payment, so PayPal didn't charge you."
    try:
        order = paypal.capture_order(order_id, f"capture-{pay.payment_number}")
    except (paypal.PayPalError, httpx.HTTPError) as e:
        if isinstance(e, paypal.PayPalError) and e.issue in ("INSTRUMENT_DECLINED", "TRANSACTION_REFUSED"):
            pay.status, pay.failure_reason = "failed", "PayPal declined the payment method."
            db.commit()
            return "PayPal declined that payment method. You were not charged; please try again with another card or account."
        db.rollback()
        return "We couldn't confirm the payment with PayPal yet. If you were charged it will show here shortly."
    return _apply_capture(db, pay, inv, paypal.first_capture(order))


def cancel_paypal(db: Session, order_id: str, client_id: str) -> None:
    pay = db.query(Payment).filter(Payment.gateway == "paypal", Payment.gateway_payment_id == order_id,
                                   Payment.client_id == client_id).first() if order_id else None
    if pay and pay.status == "pending" and not pay.transaction_reference:
        pay.status, pay.failure_reason = "failed", PAYPAL_CANCELLED
        db.commit()


# ── Admin side ────────────────────────────────────────────────────────────────

def needs_review():
    """Pending payments an admin checks by hand: UPI / bank transfers, and PayPal captures PayPal left pending.
    (A PayPal checkout with no capture yet is just an unfinished checkout, not a payment.)"""
    return (Payment.status == "pending") & or_(Payment.gateway == "manual", Payment.transaction_reference.isnot(None))


def pending_payments(db: Session) -> list[dict]:
    rows = (db.query(Payment, Invoice, TaxFiling).join(Invoice, Invoice.id == Payment.invoice_id)
            .join(TaxFiling, TaxFiling.id == Invoice.case_id).filter(needs_review())
            .order_by(Payment.created_at).all())
    return [{"payment": p, "invoice": i, "filing": f, "client_name": f"{f.first_name} {f.last_name}"} for p, i, f in rows]


def _reviewable(pay: Payment) -> bool:
    return pay.gateway == "manual" or bool(pay.transaction_reference)


def verify(db: Session, actor_id: str, pay: Payment) -> str | None:
    if not _reviewable(pay):
        return "This PayPal checkout was never completed, so there is nothing to verify."
    if pay.status != "pending":
        return "This payment has already been reviewed."
    inv = db.get(Invoice, pay.invoice_id)
    if inv.status not in OPEN:
        return f"The invoice is {inv.status.replace('_', ' ')}; reject this payment instead."
    due = balance(db, inv)
    if _money(pay.amount) > due:
        return f"This is more than the balance now due (${due:,.2f}); reject it and record the right amount."
    now = datetime.now(timezone.utc)
    pay.status, pay.paid_at, pay.verified_by_account_id, pay.verified_at = "succeeded", now, actor_id, now
    db.flush()  # sessions don't autoflush; settle() sums succeeded payments from the database
    old = inv.status
    settle(db, inv)
    _audit(db, actor_id, "payment_verified", pay.id, {"status": "pending", "invoice_status": old},
           {"status": "succeeded", "invoice_status": inv.status})
    db.commit()
    advance_case_if_paid(db, inv, actor_id)
    return None


def reject(db: Session, actor_id: str, pay: Payment, reason: str) -> str | None:
    if not _reviewable(pay):
        return "This PayPal checkout was never completed, so there is nothing to reject."
    if pay.status != "pending":
        return "This payment has already been reviewed."
    reason = " ".join((reason or "").split())[:500]
    if not reason:
        return "Give the client a reason, e.g. \"No payment found with this reference\"."
    now = datetime.now(timezone.utc)
    pay.status, pay.failure_reason, pay.verified_by_account_id, pay.verified_at = "failed", reason, actor_id, now
    _audit(db, actor_id, "payment_rejected", pay.id, {"status": "pending"}, {"status": "failed", "reason": reason})
    db.commit()
    return None
