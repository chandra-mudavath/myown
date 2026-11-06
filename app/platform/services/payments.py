"""Client payments shared by the client and admin portals.

Clients pay an issued invoice: the full balance by default, or a smaller amount they enter (the rest
stays due, and the invoice is partially paid). Two ways:

* Pay in USD with Stripe Checkout (card, Apple Pay, Google Pay, ACH bank debit), into the CovrentTax US
  account. We create a *pending* payment and a Checkout Session, and send the client to Stripe's page.
  Stripe tells us the result twice: the client's return to our success URL, and a signed webhook (which
  also covers a client who closes the tab, ACH debits that clear days later, refunds and disputes).
  Both paths read the session back from Stripe and apply it the same way, so either can come first and
  repeats change nothing. Only Stripe saying the PaymentIntent succeeded makes the payment succeeded.
  (PayPal was removed in Nov 2026; its old rows stay and still show.)
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
from urllib.parse import urlencode

import httpx
from fastapi import UploadFile
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.crypto import encrypt_bytes
from app.platform.models.billing import Invoice, Payment, PaymentEvent
from app.platform.models.system import AuditLog
from app.platform.models.tax_filing import TaxFiling
from app.platform.services import case_actions, stripe_api
from app.platform.services.catalog import case_stages
from app.platform.services.storage_service import generate_uuid, sanitize_filename

CENT = Decimal("0.01")
OPEN = ("issued", "partially_paid")
# What a client can pay with, and the currency they normally send. upi / bank_transfer are checked by an admin;
# card / ach come from Stripe (a Stripe payment is "stripe" until the client has finished checkout).
CLIENT_METHODS = {"card": ("Card / Apple Pay / Google Pay", "USD"), "ach": ("US bank account (ACH)", "USD"),
                  "upi": ("UPI (India)", "INR"), "bank_transfer": ("Bank transfer / wire", "USD")}
METHOD_LABELS = {**{k: v[0] for k, v in CLIENT_METHODS.items()}, "stripe": "Online payment", "paypal": "PayPal"}  # paypal: old rows
MANUAL_METHODS = ("upi", "bank_transfer")
STRIPE_CANCELLED = "Cancelled at checkout."
STRIPE_UNFINISHED = "Checkout wasn't completed."
STRIPE_METHOD = {"card": "card", "link": "card", "us_bank_account": "ach"}  # Stripe's payment method type -> ours
CURRENCIES = ("USD", "INR", "CAD", "GBP", "EUR", "AUD", "AED", "SGD")
PROOF_TYPES = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}
CLIENT_STATUS_LABELS = {"pending": "Awaiting verification", "succeeded": "Paid", "failed": "Not accepted", "refunded": "Refunded"}
PAID_STAGE_FROM, PAID_STAGE_TO = "payment_pending", "client_review"
MAX_ADVANCE = Decimal("100000")


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


def _advance_amount(raw: str | None) -> Decimal | str:
    """An advance payment (no invoice yet): the client types any amount (client feedback, Nov 2026)."""
    try:
        amount = Decimal(str(raw or "").replace(",", "").strip()).quantize(CENT)
    except InvalidOperation:
        return "Enter the amount to pay, e.g. 149.99."
    if amount <= 0:
        return "Enter the amount to pay."
    if amount > MAX_ADVANCE:
        return f"For more than ${MAX_ADVANCE:,.0f}, please contact your tax team."
    return amount


def _audit(db: Session, actor_id: str | None, action: str, entity_id: str, old: dict | None, new: dict | None) -> None:
    db.add(AuditLog(actor_account_id=actor_id, action=action, entity_type="payment", entity_id=entity_id,
                    old_values=old, new_values=new))


# What a payment counts towards its invoice: a part refund takes that much back off.
NET_AMOUNT = Payment.amount - func.coalesce(Payment.refunded_amount, 0)


def paid_amount(db: Session, invoice_id: str) -> Decimal:
    total = db.query(func.coalesce(func.sum(NET_AMOUNT), 0)).filter(
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
    payments = (db.query(Payment, Invoice).outerjoin(Invoice, Invoice.id == Payment.invoice_id)
                .filter(Payment.client_id == client_id, or_(Payment.case_id == filing.id, Invoice.case_id == filing.id))
                .order_by(Payment.created_at.desc()).all())
    # Online checkouts the client never finished (no PaymentIntent / capture) aren't payments; leave them out.
    payments = [(p, i) for p, i in payments if not (p.gateway in ("stripe", "paypal") and not p.transaction_reference)]
    pending_invoice_ids = {p.invoice_id for p, _ in payments if p.status == "pending" and p.invoice_id}
    advance_pending = any(p.status == "pending" and not p.invoice_id and p.gateway == "manual" for p, _ in payments)
    for row in open_invoices:
        row["pending"] = row["invoice"].id in pending_invoice_ids
    return {
        "open_invoices": open_invoices,
        "amount_due": sum((r["balance"] for r in open_invoices), Decimal("0.00")),
        "payments": [{"payment": p, "invoice": i, "status_label": _client_status(p),
                      "method_label": METHOD_LABELS.get(p.payment_method, p.payment_method.replace("_", " ").title())}
                     for p, i in payments],
        "has_pending": bool(pending_invoice_ids),
        "advance_pending": advance_pending,
    }


def _client_status(p: Payment) -> str:
    if p.gateway == "stripe" and p.status == "pending":
        return "Processing at your bank"  # an ACH debit takes a few business days to clear
    if p.gateway == "paypal" and p.status == "pending":
        return "Processing at PayPal"
    if p.status == "succeeded" and p.refunded_amount:
        return f"Paid · ${_money(p.refunded_amount):,.2f} refunded"
    return CLIENT_STATUS_LABELS.get(p.status, p.status)


def _save_proof(file: UploadFile, client_id: str, label: str) -> tuple[str, str, str] | str:
    """Store the proof privately. Returns (path, original name, mime) or an error message."""
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in PROOF_TYPES:
        return "The proof must be a PDF, PNG or JPG file."
    data = file.file.read(settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024 + 1)
    if len(data) > settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024:
        return f"The proof file is larger than {settings.MAX_UPLOAD_SIZE_MB} MB."
    if not data:
        return "The proof file is empty."
    folder = Path(settings.PAYMENT_PROOFS_DIR) / client_id
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{label}_{generate_uuid()[:8]}{ext}"
    path.write_bytes(encrypt_bytes(data))  # encrypted at rest, like documents
    return str(path), sanitize_filename(file.filename or f"proof{ext}")[:255], PROOF_TYPES[ext]


def submit_manual(db: Session, account_id: str, client_id: str, invoice_id: str, method: str, reference: str,
                  sent_amount: str, sent_currency: str, proof: UploadFile | None, pay_amount: str = "",
                  filing: TaxFiling | None = None) -> str | None:
    """A client says they paid by UPI or bank transfer. With no invoice it is an advance payment on `filing`
    (client feedback, Nov 2026: pay at any stage). Returns an error message, or None."""
    inv = db.get(Invoice, invoice_id) if invoice_id else None
    if invoice_id and (not inv or inv.client_id != client_id or inv.status not in OPEN):
        return "That invoice can't be paid right now. Refresh the page and try again."
    if not inv and (not filing or filing.client_id != client_id):
        return "Choose the tax year you are paying for, then try again."
    if method not in MANUAL_METHODS:
        return "Choose how you paid."
    ref = " ".join((reference or "").split())[:100]
    if len(ref) < 4:
        return "Enter the transaction / reference number from your payment."
    # Was: one pending manual payment per invoice at a time (client feedback, Nov 2026: no conditions).
    if db.query(Payment).filter(Payment.transaction_reference == ref, Payment.status.in_(("pending", "succeeded"))).first():
        return "That reference number has already been submitted."
    if inv:
        due = balance(db, inv)
        if due <= 0:
            return "Nothing is due on this invoice."
        amount = _pay_amount(pay_amount, due)
    else:
        amount = _advance_amount(pay_amount)
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
        saved = _save_proof(proof, client_id, inv.invoice_number if inv else filing.get_case_id(db))
        if isinstance(saved, str):
            return saved
        proof_path, proof_name, proof_mime = saved

    # The payment counts the chosen amount in the invoice currency; what was actually sent is kept alongside.
    pay = Payment(invoice_id=inv.id if inv else None, case_id=inv.case_id if inv else filing.id, client_id=client_id,
                  amount=amount, payment_method=method, transaction_reference=ref,
                  status="pending", gateway="manual", submitted_by_account_id=account_id,
                  original_amount=sent, original_currency=currency,
                  proof_path=proof_path, proof_filename=proof_name, proof_mime=proof_mime)
    db.add(pay)
    db.flush()
    _audit(db, account_id, "payment_submitted", pay.id, None,
           {"payment_number": pay.payment_number, "invoice": inv.invoice_number if inv else "advance payment",
            "amount": str(amount), "method": method, "sent": f"{sent} {currency}", "reference": ref})
    db.commit()
    return None


# ── Stripe (Pay in USD) ───────────────────────────────────────────────────────

def _locked_invoice(db: Session, invoice_id: str) -> Invoice | None:
    """The invoice row, locked until commit so two confirmations can't both settle the same balance."""
    return db.query(Invoice).filter(Invoice.id == invoice_id).with_for_update().first()


def _close_checkout(pay: Payment, reason: str) -> None:
    """Close an unpaid Checkout page at Stripe, then mark the payment failed. The caller commits.

    If Stripe says the page isn't open any more (the client paid in another tab, or it already expired) the payment
    is left alone: the return page, the webhook or an admin's "Check with Stripe" records what really happened."""
    if pay.gateway_payment_id:
        try:
            stripe_api.expire_checkout_session(pay.gateway_payment_id)
        except stripe_api.StripeError:
            return
        except httpx.HTTPError:
            pass  # can't reach Stripe: the page expires by itself within CHECKOUT_MINUTES
    pay.status, pay.failure_reason = "failed", reason


def start_stripe(db: Session, account_id: str, client_id: str, invoice_id: str, success_url: str, cancel_url: str,
                 pay_amount: str = "", filing: TaxFiling | None = None, email: str = "") -> tuple[str | None, str | None]:
    """Create a Stripe Checkout page for an invoice's balance (or the part the client chose), or, with no invoice, an
    advance payment on `filing`. Returns (URL to send the client to, error)."""
    if not stripe_api.enabled():
        return None, "Card payment isn't available right now. Please use Pay in INR, or contact your tax team."
    inv = db.get(Invoice, invoice_id) if invoice_id else None
    if invoice_id and (not inv or inv.client_id != client_id or inv.status not in OPEN):
        return None, "That invoice can't be paid right now. Refresh the page and try again."
    if not inv and (not filing or filing.client_id != client_id):
        return None, "Choose the tax year you are paying for, then try again."
    if inv:
        due = balance(db, inv)
        if due <= 0:
            return None, "Nothing is due on this invoice."
        amount = _pay_amount(pay_amount, due)
    else:
        amount = _advance_amount(pay_amount)
    if isinstance(amount, str):
        return None, amount
    # An earlier checkout the client walked away from is closed, so only the newest page can take money.
    same = (Payment.invoice_id == inv.id) if inv else ((Payment.case_id == filing.id) & Payment.invoice_id.is_(None))
    for old in db.query(Payment).filter(same, Payment.gateway == "stripe", Payment.status == "pending",
                                        Payment.transaction_reference.is_(None)).all():
        _close_checkout(old, STRIPE_UNFINISHED)
    reference = inv.invoice_number if inv else filing.get_case_id(db)
    pay = Payment(invoice_id=inv.id if inv else None, case_id=inv.case_id if inv else filing.id, client_id=client_id,
                  amount=amount, payment_method="stripe", status="pending", gateway="stripe",
                  submitted_by_account_id=account_id, original_amount=amount, original_currency="USD")
    db.add(pay)
    db.flush()
    try:
        session_id, url = stripe_api.create_checkout_session(
            amount=amount, payment_id=pay.id, payment_number=pay.payment_number, reference=reference,
            description=f"{settings.APP_NAME} {'invoice' if inv else 'advance payment'} {reference}",
            customer_email=(email or "").strip()[:255], success_url=success_url,
            cancel_url=f"{cancel_url}?{urlencode({'payment': pay.id})}")
    except (stripe_api.StripeError, httpx.HTTPError):
        db.rollback()
        return None, "We couldn't reach the payment service. You were not charged; please try again in a few minutes."
    pay.gateway_payment_id = session_id
    _audit(db, account_id, "stripe_checkout_started", pay.id, None,
           {"payment_number": pay.payment_number, "invoice": reference, "amount": str(amount), "session": session_id})
    db.commit()
    return url, None


def _expanded(value) -> dict:
    """Stripe sends a related object either expanded (a dict) or as its id (a string)."""
    return value if isinstance(value, dict) else ({"id": value} if value else {})


def _mark_paid(db: Session, pay: Payment, session: dict, charge: dict) -> None:
    inv = _locked_invoice(db, pay.invoice_id) if pay.invoice_id else None  # None: an advance payment
    if session.get("amount_total") is not None:
        pay.original_amount = (Decimal(session["amount_total"]) / 100).quantize(CENT)
    pay.original_currency = (session.get("currency") or "usd").upper()[:3]
    txn = _expanded(charge.get("balance_transaction"))
    if txn.get("fee") is not None:
        pay.fee_amount = (Decimal(txn["fee"]) / 100).quantize(CENT)
    late = pay.status == "failed"  # closed on our side, but the client had already paid in another tab
    pay.status, pay.paid_at, pay.failure_reason = "succeeded", datetime.now(timezone.utc), None
    db.flush()  # settle() sums succeeded payments from the database
    old = inv.status if inv else None
    if inv:
        settle(db, inv)
    new = {"payment_intent": pay.transaction_reference, "amount": str(pay.original_amount),
           "method": pay.payment_method, "invoice_status": inv.status if inv else "advance payment"}
    if late:
        new["note"] = "paid after the checkout was closed"
    if inv and paid_amount(db, inv.id) > _money(inv.total_amount):
        new["overpaid_by"] = str(paid_amount(db, inv.id) - _money(inv.total_amount))  # an admin can refund it
    _audit(db, None, "stripe_payment_succeeded", pay.id, {"invoice_status": old}, new)
    db.commit()
    if inv:
        advance_case_if_paid(db, inv, None)


def _apply_session(db: Session, pay: Payment, session: dict) -> str:
    """Record what Stripe says about a Checkout Session (read back with get_checkout_session). Commits.

    Returns "succeeded", "processing" (an ACH debit still clearing), "failed" or "open" (not finished yet)."""
    if session.get("status") == "open":
        return "open"
    if session.get("status") == "expired":
        if pay.status == "pending" and not pay.transaction_reference:
            pay.status, pay.failure_reason = "failed", STRIPE_UNFINISHED
        db.commit()
        return "failed"
    intent = _expanded(session.get("payment_intent"))
    charge = _expanded(intent.get("latest_charge"))
    if intent.get("id"):
        pay.transaction_reference = intent["id"][:100]
    kind = (charge.get("payment_method_details") or {}).get("type") or (intent.get("payment_method_types") or [""])[0]
    if pay.payment_method == "stripe" and kind:
        pay.payment_method = STRIPE_METHOD.get(kind, "stripe")
    state = intent.get("status") or ("succeeded" if session.get("payment_status") == "paid" else "processing")
    if state == "succeeded":
        if pay.status not in ("succeeded", "refunded"):
            _mark_paid(db, pay, session, charge)
        else:
            db.commit()
        return "succeeded"
    if state == "processing":
        if pay.status == "pending":
            _audit(db, None, "stripe_payment_processing", pay.id, None, {"payment_intent": pay.transaction_reference})
        db.commit()
        return "processing"
    # requires_payment_method / canceled: the card or bank debit didn't go through
    if pay.status == "pending":
        message = (intent.get("last_payment_error") or {}).get("message") or "The payment didn't go through."
        pay.status, pay.failure_reason = "failed", message[:500]
        _audit(db, None, "stripe_payment_failed", pay.id, None, {"payment_intent": pay.transaction_reference,
                                                                 "stripe_status": state, "reason": pay.failure_reason})
    db.commit()
    return "failed"


PAID_MESSAGE = "Thank you! Your payment was received."
PROCESSING_MESSAGE = ("Thank you! Your bank payment has started. US bank (ACH) payments take 3-5 business days to clear; "
                      "it shows as processing until then.")


def finish_stripe(db: Session, session_id: str, client_id: str | None = None) -> tuple[str | None, str]:
    """The client is back from Stripe Checkout: read the session and record it. Safe to call twice (a refreshed page).
    Returns (error, message for the client)."""
    pay = (db.query(Payment).filter(Payment.gateway == "stripe", Payment.gateway_payment_id == session_id).first()
           if session_id else None)
    if not pay or (client_id and pay.client_id != client_id):
        return "We couldn't find that payment.", ""
    if pay.status in ("succeeded", "refunded"):
        return None, PAID_MESSAGE
    try:
        session = stripe_api.get_checkout_session(session_id)
    except (stripe_api.StripeError, httpx.HTTPError):
        return None, "Thank you! We're confirming your payment; it will show here in a few minutes."
    state = _apply_session(db, pay, session)
    if state == "succeeded":
        return None, PAID_MESSAGE
    if state == "processing":
        return None, PROCESSING_MESSAGE
    if state == "open":
        return "The payment wasn't finished. You were not charged; please try again.", ""
    return f"{pay.failure_reason or 'The payment did not go through.'} You were not charged; please try again or use another method.", ""


def cancel_stripe(db: Session, payment_id: str, client_id: str) -> None:
    """The client pressed back on Stripe's page: close that page so it can't be paid later."""
    pay = db.get(Payment, payment_id) if payment_id else None
    if pay and pay.client_id == client_id and pay.gateway == "stripe" and pay.status == "pending" and not pay.transaction_reference:
        _close_checkout(pay, STRIPE_CANCELLED)
        db.commit()


def _apply_refund(db: Session, pay: Payment, refunded_total: Decimal, actor_id: str | None, refund_id: str | None) -> None:
    """Set how much of a Stripe payment has been refunded in all (from our refund, or Stripe's charge.refunded). Commits."""
    inv = _locked_invoice(db, pay.invoice_id) if pay.invoice_id else None
    refunded_total = min(_money(refunded_total), _money(pay.amount))
    if refunded_total == _money(pay.refunded_amount) and (pay.status == "refunded") == (refunded_total >= _money(pay.amount)):
        db.commit()
        return  # nothing new (e.g. the webhook for a refund already recorded here)
    old = {"status": pay.status, "refunded": str(_money(pay.refunded_amount)), "invoice_status": inv.status if inv else None}
    pay.refunded_amount = refunded_total
    if pay.status == "succeeded" and refunded_total >= _money(pay.amount):
        pay.status = "refunded"
    db.flush()
    if inv:
        settle(db, inv)
    _audit(db, actor_id, "stripe_payment_refunded", pay.id, old,
           {"status": pay.status, "refunded": str(refunded_total), "refund": refund_id, "invoice_status": inv.status if inv else None})
    db.commit()


def refund(db: Session, actor_id: str, pay: Payment, amount_raw: str) -> str | None:
    """An admin refunds all or part of a Stripe payment back to the client's card / bank. Returns an error or None."""
    if pay.gateway != "stripe" or not pay.transaction_reference:
        return "Only card / ACH payments made through Stripe can be refunded here."
    if pay.status != "succeeded":
        return "Only a received payment can be refunded."
    left = _money(pay.amount) - _money(pay.refunded_amount)
    if left <= 0:
        return "This payment has already been refunded in full."
    amount = _pay_amount(amount_raw, left)
    if isinstance(amount, str):
        return amount.replace("balance due", "amount left to refund")
    try:
        result = stripe_api.create_refund(pay.transaction_reference, amount,
                                          f"refund-{pay.payment_number}-{stripe_api.cents(_money(pay.refunded_amount))}-{stripe_api.cents(amount)}")
    except stripe_api.StripeError as e:
        return f"Stripe didn't make the refund ({e.code or 'error'}). Nothing was refunded."
    except httpx.HTTPError:
        return "We couldn't reach Stripe. Nothing was refunded; please try again."
    _apply_refund(db, pay, _money(pay.refunded_amount) + amount, actor_id, result.get("id"))
    return None


def _apply_dispute(db: Session, pay: Payment, dispute: dict) -> None:
    """The client's bank opened (or closed) a dispute on a card payment. A lost dispute takes the money back. Commits."""
    status = (dispute.get("status") or "needs_response")[:30]  # warning_needs_response, needs_response, under_review, won, lost
    old = pay.dispute_status
    pay.dispute_status = status
    _audit(db, None, "stripe_dispute", pay.id, {"dispute_status": old},
           {"dispute_status": status, "dispute": dispute.get("id"), "reason": dispute.get("reason")})
    if status == "lost":
        _apply_refund(db, pay, _money(pay.amount), None, dispute.get("id"))
    else:
        db.commit()


def sync_stripe(db: Session, pay: Payment) -> str | None:
    """An admin's "Check with Stripe": read the payment from Stripe and record it (if a webhook was missed)."""
    if pay.gateway != "stripe" or not pay.gateway_payment_id:
        return "This isn't a Stripe payment."
    try:
        session = stripe_api.get_checkout_session(pay.gateway_payment_id)
    except (stripe_api.StripeError, httpx.HTTPError):
        return "We couldn't reach Stripe. Please try again."
    _apply_session(db, pay, session)
    return None


def _by_intent(db: Session, intent_id) -> Payment | None:
    if not isinstance(intent_id, str) or not intent_id:
        return None
    return db.query(Payment).filter(Payment.gateway == "stripe", Payment.transaction_reference == intent_id).first()


def handle_stripe_event(db: Session, event: dict) -> str:
    """Apply one signed webhook event (already checked by stripe_api.verify_event). Safe to receive twice.

    Checkout events are not trusted as sent: the session is read back from Stripe and applied like the return page.
    Raises when Stripe can't be reached, so the webhook answers 500 and Stripe sends it again later.
    Returns "applied", "ignored" (not one of our payments / not an event we use) or "duplicate"."""
    event_id, kind = str(event.get("id") or ""), str(event.get("type") or "")
    obj = (event.get("data") or {}).get("object") or {}
    if not event_id:
        return "ignored"
    if db.query(PaymentEvent).filter(PaymentEvent.gateway == "stripe", PaymentEvent.event_id == event_id).first():
        return "duplicate"
    pay = None
    if kind in ("checkout.session.completed", "checkout.session.async_payment_succeeded",
                "checkout.session.async_payment_failed", "checkout.session.expired"):
        pay = db.query(Payment).filter(Payment.gateway == "stripe", Payment.gateway_payment_id == obj.get("id")).first()
        if pay:
            _apply_session(db, pay, stripe_api.get_checkout_session(pay.gateway_payment_id))
    elif kind == "charge.refunded":
        pay = _by_intent(db, obj.get("payment_intent"))
        if pay:
            _apply_refund(db, pay, Decimal(obj.get("amount_refunded") or 0) / 100, None, None)
    elif kind in ("charge.dispute.created", "charge.dispute.updated", "charge.dispute.closed"):
        pay = _by_intent(db, obj.get("payment_intent"))
        if pay:
            _apply_dispute(db, pay, obj)
    db.add(PaymentEvent(gateway="stripe", event_id=event_id[:100], event_type=kind[:100], payment_id=pay.id if pay else None))
    try:
        db.commit()
    except IntegrityError:  # the same event arrived twice at once; the other request recorded it
        db.rollback()
        return "duplicate"
    return "applied" if pay else "ignored"


# ── Admin side ────────────────────────────────────────────────────────────────

def needs_review():
    """Pending payments an admin checks by hand: UPI / bank transfers (and old PayPal captures PayPal left pending).
    Stripe payments are confirmed by Stripe itself (return page / webhook), so they never wait for an admin."""
    return (Payment.status == "pending") & or_(
        Payment.gateway == "manual", (Payment.gateway == "paypal") & Payment.transaction_reference.isnot(None))


def pending_payments(db: Session) -> list[dict]:
    rows = (db.query(Payment, Invoice, TaxFiling).outerjoin(Invoice, Invoice.id == Payment.invoice_id)
            .join(TaxFiling, TaxFiling.id == func.coalesce(Payment.case_id, Invoice.case_id)).filter(needs_review())
            .order_by(Payment.created_at).all())
    return [{"payment": p, "invoice": i, "filing": f, "client_name": f"{f.first_name} {f.last_name}"} for p, i, f in rows]


def _reviewable(pay: Payment) -> bool:
    return pay.gateway == "manual" or (pay.gateway == "paypal" and bool(pay.transaction_reference))


def verify(db: Session, actor_id: str, pay: Payment) -> str | None:
    if not _reviewable(pay):
        return "Online payments are confirmed by the payment service; use Check with Stripe instead."
    if pay.status != "pending":
        return "This payment has already been reviewed."
    inv = db.get(Invoice, pay.invoice_id) if pay.invoice_id else None  # None: an advance payment
    if inv and inv.status not in OPEN:
        return f"The invoice is {inv.status.replace('_', ' ')}; reject this payment instead."
    if inv:
        due = balance(db, inv)
        if _money(pay.amount) > due:
            return f"This is more than the balance now due (${due:,.2f}); reject it and record the right amount."
    now = datetime.now(timezone.utc)
    pay.status, pay.paid_at, pay.verified_by_account_id, pay.verified_at = "succeeded", now, actor_id, now
    db.flush()  # sessions don't autoflush; settle() sums succeeded payments from the database
    old = inv.status if inv else None
    if inv:
        settle(db, inv)
    _audit(db, actor_id, "payment_verified", pay.id, {"status": "pending", "invoice_status": old},
           {"status": "succeeded", "invoice_status": inv.status if inv else "advance payment"})
    db.commit()
    if inv:
        advance_case_if_paid(db, inv, actor_id)
    return None


def attach_advance_payments(db: Session, inv: Invoice) -> int:
    """When an invoice is issued, the case's advance payments (made before any invoice) count towards it.
    Pending ones move with it and are settled once an admin verifies them. Flushes; the caller commits.
    Returns how many were attached."""
    advances = (db.query(Payment).filter(Payment.case_id == inv.case_id, Payment.invoice_id.is_(None),
                                         Payment.status.in_(("pending", "succeeded")))
                .order_by(Payment.created_at).all())
    for pay in advances:
        pay.invoice_id = inv.id
    if advances:
        db.flush()
        settle(db, inv)
        _audit(db, None, "advance_payments_attached", inv.id, None,
               {"invoice": inv.invoice_number, "payments": [p.payment_number for p in advances]})
    return len(advances)


def reject(db: Session, actor_id: str, pay: Payment, reason: str) -> str | None:
    if not _reviewable(pay):
        return "Online payments are confirmed by the payment service; use Check with Stripe instead."
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
