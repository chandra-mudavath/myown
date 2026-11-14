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
* Pay in INR with Razorpay Checkout (UPI / QR, cards, net banking, wallets) into the CovrentTax India account, at
  the USD -> INR rate an admin sets; bank transfers (NEFT / RTGS / IMPS) go to the client's own Smart Collect
  account number and are recorded when Razorpay reports them. Confirmed by the signed Checkout result and webhooks.
* UPI or bank transfer submitted by hand (the fallback while Razorpay isn't set up): the client submits the reference here, which creates a *pending* payment.
  An admin verifies it (it becomes succeeded and counts towards the invoice) or rejects it
  (status failed, with the reason shown to the client).

When an invoice is paid in full and its case is waiting at payment_pending, the case moves on to
client_review so the client can see their return.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
import io
import re
from urllib.parse import quote, urlencode

import httpx
import segno
from fastapi import UploadFile
from sqlalchemy import func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.platform.models.billing import Invoice, Payment, PaymentEvent, VirtualAccount
from app.platform.models.system import AppSetting, AuditLog
from app.platform.models.tax_filing import TaxFiling
from app.platform.services import case_actions, razorpay_api, stripe_api
from app.platform.services.catalog import case_stages
from app.platform.services.storage_provider import storage_provider
from app.platform.services.storage_service import generate_uuid, sanitize_filename

CENT = Decimal("0.01")
OPEN = ("issued", "partially_paid")
# What a client can pay with, and the currency they normally send. upi / bank_transfer are checked by an admin;
# card / ach come from Stripe (a Stripe payment is "stripe" until the client has finished checkout).
CLIENT_METHODS = {"card": ("Card / Apple Pay / Google Pay", "USD"), "ach": ("US bank account (ACH)", "USD"),
                  "upi": ("UPI (India)", "INR"), "bank_transfer": ("Bank transfer / wire", "USD")}
METHOD_LABELS = {**{k: v[0] for k, v in CLIENT_METHODS.items()}, "stripe": "Online payment", "paypal": "PayPal",  # paypal: old rows
                 "card_inr": "Debit / credit card (INR)", "netbanking": "Net banking", "wallet": "Wallet", "emi": "Card EMI",
                 "paylater": "Pay later", "razorpay": "Online payment (INR)"}
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
    """The USD amount the client typed (required: no default amount, client feedback Nov 2026), or an error message.
    Was: blank meant the full balance."""
    if not (raw or "").strip():
        return "Enter the amount to pay."
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
    payments = [(p, i) for p, i in payments if not (p.gateway in ("stripe", "razorpay", "paypal") and not p.transaction_reference)]
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


# ── Pay in INR without a gateway: UPI QR with the amount filled in ────────────

UPI_NOTE = re.compile(r"[^A-Za-z0-9 -]")


def upi_link(amount_inr: str | None = None, note: str | None = None) -> str | None:
    """A UPI payment link (what the QR holds; it also opens a UPI app on a phone). None without a UPI ID.
    Any UPI app reads it: payee, name, rupee amount and a note the client sees and we match the payment by."""
    if not settings.PAYMENT_UPI_ID:
        return None
    parts = [("pa", settings.PAYMENT_UPI_ID), ("pn", settings.PAYMENT_UPI_PAYEE or settings.APP_NAME)]
    try:
        amount = Decimal(str(amount_inr or "").replace(",", "").strip()).quantize(CENT)
    except InvalidOperation:
        amount = None
    if amount and Decimal("1") <= amount <= Decimal("10000000"):
        parts.append(("am", f"{amount:.2f}"))
    parts.append(("cu", "INR"))
    note = UPI_NOTE.sub("", note or "")[:50].strip()
    if note:
        parts.append(("tn", note))
    return "upi://pay?" + "&".join(f"{k}={quote(v, safe='@.-')}" for k, v in parts)


def upi_qr_svg(link: str) -> bytes:
    buf = io.BytesIO()
    segno.make(link, error="m").save(buf, kind="svg", scale=5, border=2, dark="#0f172a", xmldecl=False)
    return buf.getvalue()


def _client_status(p: Payment) -> str:
    if p.gateway == "stripe" and p.status == "pending":
        return "Processing at your bank"  # an ACH debit takes a few business days to clear
    if p.gateway == "razorpay" and p.status == "pending":
        return "Processing"
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
    key = str(Path(settings.PAYMENT_PROOFS_DIR) / client_id / f"{label}_{generate_uuid()[:8]}{ext}")
    path = storage_provider.save(io.BytesIO(data), key, content_type=PROOF_TYPES[ext])  # encrypted at rest, like documents
    return path, sanitize_filename(file.filename or f"proof{ext}")[:255], PROOF_TYPES[ext]


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


# ── Online payments (shared by Stripe and Razorpay) ───────────────────────────

def _locked_invoice(db: Session, invoice_id: str) -> Invoice | None:
    """The invoice row, locked until commit so two confirmations can't both settle the same balance."""
    return db.query(Invoice).filter(Invoice.id == invoice_id).with_for_update().first()


def _what_to_pay(db: Session, client_id: str, invoice_id: str, pay_amount: str,
                 filing: TaxFiling | None) -> tuple[Invoice | None, Decimal | None, str | None]:
    """The invoice being paid (None: an advance payment on `filing`) and the USD amount, or an error message."""
    inv = db.get(Invoice, invoice_id) if invoice_id else None
    if invoice_id and (not inv or inv.client_id != client_id or inv.status not in OPEN):
        return None, None, "That invoice can't be paid right now. Refresh the page and try again."
    if not inv and (not filing or filing.client_id != client_id):
        return None, None, "Choose the tax year you are paying for, then try again."
    if inv:
        due = balance(db, inv)
        if due <= 0:
            return None, None, "Nothing is due on this invoice."
        amount = _pay_amount(pay_amount, due)
    else:
        amount = _advance_amount(pay_amount)
    if isinstance(amount, str):
        return None, None, amount
    return inv, amount, None


def _record_success(db: Session, pay: Payment, action: str, details: dict) -> None:
    """The gateway says the money arrived: mark the payment succeeded, settle its invoice, move the case on. Commits.

    Also used when a checkout we had closed or marked cancelled is paid after all (money moved, so it counts)."""
    inv = _locked_invoice(db, pay.invoice_id) if pay.invoice_id else None  # None: an advance payment
    late = pay.status == "failed"
    pay.status, pay.paid_at, pay.failure_reason = "succeeded", datetime.now(timezone.utc), None
    db.flush()  # settle() sums succeeded payments from the database
    old = inv.status if inv else None
    if inv:
        settle(db, inv)
    new = {**details, "amount": str(pay.amount), "paid": f"{pay.original_amount} {pay.original_currency}",
           "method": pay.payment_method, "invoice_status": inv.status if inv else "advance payment"}
    if late:
        new["note"] = "paid after the checkout was closed"
    if inv and paid_amount(db, inv.id) > _money(inv.total_amount):
        new["overpaid_by"] = str(paid_amount(db, inv.id) - _money(inv.total_amount))  # an admin can refund it
    _audit(db, None, action, pay.id, {"invoice_status": old}, new)
    db.commit()
    if inv:
        advance_case_if_paid(db, inv, None)


# ── Stripe (Pay in USD) ───────────────────────────────────────────────────────


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
    inv, amount, err = _what_to_pay(db, client_id, invoice_id, pay_amount, filing)
    if err:
        return None, err
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
    if session.get("amount_total") is not None:
        pay.original_amount = (Decimal(session["amount_total"]) / 100).quantize(CENT)
    pay.original_currency = (session.get("currency") or "usd").upper()[:3]
    txn = _expanded(charge.get("balance_transaction"))
    if txn.get("fee") is not None:
        pay.fee_amount = (Decimal(txn["fee"]) / 100).quantize(CENT)
    _record_success(db, pay, "stripe_payment_succeeded", {"payment_intent": pay.transaction_reference})


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
    """Set how much of an online payment has been refunded in all, in USD (from our refund, or the gateway's webhook). Commits."""
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
    _audit(db, actor_id, f"{pay.gateway}_payment_refunded", pay.id, old,
           {"status": pay.status, "refunded": str(refunded_total), "refund": refund_id, "invoice_status": inv.status if inv else None})
    db.commit()


def refund(db: Session, actor_id: str, pay: Payment, amount_raw: str) -> str | None:
    """An admin refunds all or part of an online payment (amount in USD) back to how the client paid.
    Returns an error or None."""
    if pay.gateway not in ("stripe", "razorpay") or not pay.transaction_reference:
        return "Only payments made online (Stripe or Razorpay) can be refunded here."
    if pay.status != "succeeded":
        return "Only a received payment can be refunded."
    left = _money(pay.amount) - _money(pay.refunded_amount)
    if left <= 0:
        return "This payment has already been refunded in full."
    amount = _pay_amount(amount_raw, left)  # required: was "blank refunds it all"
    if isinstance(amount, str):
        return amount.replace("balance due", "amount left to refund").replace("amount to pay", "amount to refund")
    if pay.gateway == "razorpay":
        return _refund_razorpay(db, actor_id, pay, amount, left)
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
    """The client's bank opened (or closed) a dispute on a payment. A lost dispute takes the money back. Commits.
    Stripe: warning_needs_response, needs_response, under_review, won, lost. Razorpay: open, under_review, won, lost, closed."""
    status = (dispute.get("status") or "needs_response")[:30]
    old = pay.dispute_status
    pay.dispute_status = status
    _audit(db, None, f"{pay.gateway}_dispute", pay.id, {"dispute_status": old},
           {"dispute_status": status, "dispute": dispute.get("id"), "reason": dispute.get("reason")})
    if status == "lost":
        _apply_refund(db, pay, _money(pay.amount), None, dispute.get("id"))
    else:
        db.commit()


def sync_gateway(db: Session, pay: Payment) -> str | None:
    """An admin's "Check with Stripe / Razorpay" on a payment, for when a webhook was missed."""
    if pay.gateway == "razorpay":
        return sync_razorpay(db, pay)
    return sync_stripe(db, pay)


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


# ── Razorpay (Pay in INR) ─────────────────────────────────────────────────────
# The invoice stays in USD. The client pays the rupee amount at the admin-set USD -> INR rate, which is locked on the
# payment (amount = USD it counts for, original_amount = rupees paid). Checkout is Razorpay's window on our page; a
# signed result comes back to us from the browser and, separately, by webhook. Orders don't expire, so a payment that
# arrives after the client closed the window is still recorded.

RATE_KEY = "usd_inr_rate"
CHECKOUT_CANCELLED = "Cancelled at checkout."
RZP_METHOD = {"upi": "upi", "card": "card_inr", "netbanking": "netbanking", "wallet": "wallet", "emi": "emi",
              "paylater": "paylater", "bank_transfer": "bank_transfer"}  # Razorpay's method -> ours


def inr_rate(db: Session) -> Decimal | None:
    """Rupees for one US dollar, as set by an admin (None until set)."""
    row = db.get(AppSetting, RATE_KEY)
    try:
        rate = Decimal(row.value) if row and row.value else None
    except InvalidOperation:
        return None
    return rate if rate and rate > 0 else None


def inr_rate_info(db: Session) -> dict:
    row = db.get(AppSetting, RATE_KEY)
    return {"inr_rate": inr_rate(db), "inr_rate_updated": row.updated_at if row and row.value else None}


def set_inr_rate(db: Session, actor_id: str, raw: str) -> str | None:
    try:
        rate = Decimal(str(raw or "").replace(",", "").strip()).quantize(Decimal("0.0001"))
    except InvalidOperation:
        return "Enter the rupees for one US dollar, e.g. 88.50."
    if not Decimal("1") <= rate <= Decimal("1000"):
        return "Enter the rupees for one US dollar, e.g. 88.50."
    row = db.get(AppSetting, RATE_KEY)
    old = row.value if row else None
    if row:
        row.value, row.updated_by_account_id, row.updated_at = str(rate), actor_id, datetime.now(timezone.utc)
    else:
        db.add(AppSetting(key=RATE_KEY, value=str(rate), updated_by_account_id=actor_id, updated_at=datetime.now(timezone.utc)))
    db.add(AuditLog(actor_account_id=actor_id, action="inr_rate_changed", entity_type="setting", entity_id=RATE_KEY,
                    old_values={"rate": old}, new_values={"rate": str(rate)}))
    db.commit()
    return None


def inr_for(usd: Decimal, rate: Decimal) -> Decimal:
    return (usd * rate).quantize(CENT, rounding=ROUND_HALF_UP)


def razorpay_ready(db: Session) -> bool:
    """Pay in INR online: keys in .env and a rate set by an admin."""
    return razorpay_api.enabled() and inr_rate(db) is not None


def start_razorpay(db: Session, account_id: str, client_id: str, invoice_id: str, pay_amount: str = "",
                   filing: TaxFiling | None = None, email: str = "", phone: str = "", name: str = "") -> tuple[dict | None, str | None]:
    """Create a Razorpay order for the rupee amount of what the client chose to pay. Returns (what the browser needs to
    open Checkout, error)."""
    rate = inr_rate(db)
    if not razorpay_api.enabled() or rate is None:
        return None, "Online payment in INR isn't available right now. Please contact your tax team."
    inv, amount, err = _what_to_pay(db, client_id, invoice_id, pay_amount, filing)
    if err:
        return None, err
    rupees = inr_for(amount, rate)
    # An earlier Checkout the client closed without paying no longer shows; it still counts if it is paid after all.
    same = (Payment.invoice_id == inv.id) if inv else ((Payment.case_id == filing.id) & Payment.invoice_id.is_(None))
    for old in db.query(Payment).filter(same, Payment.gateway == "razorpay", Payment.status == "pending",
                                        Payment.transaction_reference.is_(None)).all():
        old.status, old.failure_reason = "failed", STRIPE_UNFINISHED
    reference = inv.invoice_number if inv else filing.get_case_id(db)
    pay = Payment(invoice_id=inv.id if inv else None, case_id=inv.case_id if inv else filing.id, client_id=client_id,
                  amount=amount, payment_method="razorpay", status="pending", gateway="razorpay",
                  submitted_by_account_id=account_id, original_amount=rupees, original_currency="INR")
    db.add(pay)
    db.flush()
    try:
        order = razorpay_api.create_order(amount_inr=rupees, receipt=pay.payment_number, notes={
            "payment_id": pay.id, "payment_number": pay.payment_number, "reference": reference,
            "usd": str(amount), "rate": str(rate)})
    except (razorpay_api.RazorpayError, httpx.HTTPError):
        db.rollback()
        return None, "We couldn't reach the payment service. You were not charged; please try again in a few minutes."
    pay.gateway_payment_id = order["id"]
    _audit(db, account_id, "razorpay_checkout_started", pay.id, None,
           {"payment_number": pay.payment_number, "invoice": reference, "usd": str(amount), "inr": str(rupees),
            "rate": str(rate), "order": order["id"]})
    db.commit()
    return {
        "key": settings.RAZORPAY_KEY_ID, "order_id": order["id"], "amount": razorpay_api.paise(rupees), "currency": "INR",
        "name": settings.APP_NAME, "description": f"{'Invoice' if inv else 'Advance payment'} {reference}",
        "prefill": {"name": name[:100], "email": (email or "")[:255], "contact": (phone or "")[:25]},
        "notes": {"payment_number": pay.payment_number}, "payment": pay.id,
    }, None


def _apply_razorpay_payment(db: Session, pay: Payment, rp: dict) -> str:
    """Record what Razorpay says about one payment on this order (a payment entity). Commits.
    Returns "succeeded", "processing", "failed" or "open"."""
    status = rp.get("status", "")
    if status == "authorized":  # auto-capture is off in the Dashboard (or not run yet): capture it ourselves
        try:
            rp = razorpay_api.capture_payment(rp["id"], int(rp.get("amount") or 0)) or rp
            status = rp.get("status", status)
        except (razorpay_api.RazorpayError, httpx.HTTPError):
            pass  # left processing; the payment.captured webhook or "Check with Razorpay" finishes it
    if status in ("captured", "refunded"):
        if pay.status in ("succeeded", "refunded"):
            db.commit()
            return "succeeded"
        pay.transaction_reference = rp["id"][:100]
        pay.payment_method = RZP_METHOD.get(rp.get("method", ""), "razorpay")
        if rp.get("amount") is not None:
            pay.original_amount = (Decimal(rp["amount"]) / 100).quantize(CENT)
        pay.original_currency = (rp.get("currency") or "INR").upper()[:3]
        if rp.get("fee") is not None:
            pay.fee_amount = (Decimal(rp["fee"]) / 100).quantize(CENT)  # includes GST on the fee
        _record_success(db, pay, "razorpay_payment_captured", {"razorpay_payment": rp["id"]})
        return "succeeded"
    if status == "authorized":
        if pay.status == "pending" and not pay.transaction_reference:
            pay.transaction_reference = rp["id"][:100]
            pay.payment_method = RZP_METHOD.get(rp.get("method", ""), "razorpay")
            _audit(db, None, "razorpay_payment_authorized", pay.id, None, {"razorpay_payment": rp["id"]})
        db.commit()
        return "processing"
    if status == "failed":
        # One failed attempt; the client may still pay on the same order (a webhook then marks it paid).
        if pay.status == "pending" and not pay.transaction_reference:
            pay.status = "failed"
            pay.failure_reason = (rp.get("error_description") or "The payment didn't go through.")[:500]
            _audit(db, None, "razorpay_payment_failed", pay.id, None,
                   {"razorpay_payment": rp.get("id"), "reason": pay.failure_reason, "code": rp.get("error_code")})
        db.commit()
        return "failed"
    db.commit()
    return "open"


def finish_razorpay(db: Session, order_id: str, payment_id: str, signature: str, client_id: str | None = None) -> tuple[str | None, str]:
    """Checkout says the client paid: check the signature, read the payment from Razorpay and record it.
    Safe to call twice. Returns (error, message for the client)."""
    pay = (db.query(Payment).filter(Payment.gateway == "razorpay", Payment.gateway_payment_id == order_id).first()
           if order_id else None)
    if not pay or (client_id and pay.client_id != client_id):
        return "We couldn't find that payment.", ""
    if not razorpay_api.checkout_signature_ok(order_id, payment_id, signature):
        return "We couldn't confirm that payment. If money left your account it will show here shortly.", ""
    if pay.status in ("succeeded", "refunded"):
        return None, PAID_MESSAGE
    try:
        rp = razorpay_api.get_payment(payment_id)
    except (razorpay_api.RazorpayError, httpx.HTTPError):
        return None, "Thank you! We're confirming your payment; it will show here in a few minutes."
    if rp.get("order_id") != order_id:
        return "We couldn't confirm that payment. If money left your account it will show here shortly.", ""
    state = _apply_razorpay_payment(db, pay, rp)
    if state == "succeeded":
        return None, PAID_MESSAGE
    if state == "processing":
        return None, "Thank you! Your payment is being confirmed by your bank; it will show here shortly."
    return f"{pay.failure_reason or 'The payment did not go through.'} You were not charged; please try again or use another method.", ""


def cancel_razorpay(db: Session, payment_id: str, client_id: str) -> None:
    """The client closed the Checkout window without paying."""
    pay = db.get(Payment, payment_id) if payment_id else None
    if pay and pay.client_id == client_id and pay.gateway == "razorpay" and pay.status == "pending" and not pay.transaction_reference:
        pay.status, pay.failure_reason = "failed", CHECKOUT_CANCELLED
        db.commit()


def _usd_share(pay: Payment, refunded_paise: int) -> Decimal:
    """The USD a refund of `refunded_paise` stands for, at the rate the client paid."""
    paid_paise = razorpay_api.paise(_money(pay.original_amount))
    if not paid_paise or refunded_paise >= paid_paise:
        return _money(pay.amount)
    return (_money(pay.amount) * refunded_paise / paid_paise).quantize(CENT, rounding=ROUND_HALF_UP)


def _refund_razorpay(db: Session, actor_id: str, pay: Payment, amount: Decimal, left: Decimal) -> str | None:
    paid_paise = razorpay_api.paise(_money(pay.original_amount))
    if amount >= left:  # the rest: exactly what is still unrefunded at Razorpay
        try:
            already = int(razorpay_api.get_payment(pay.transaction_reference).get("amount_refunded") or 0)
        except (razorpay_api.RazorpayError, httpx.HTTPError):
            return "We couldn't reach Razorpay. Nothing was refunded; please try again."
        rupee_paise = paid_paise - already
    else:
        rupee_paise = int((Decimal(paid_paise) * amount / _money(pay.amount)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    if rupee_paise <= 0:
        return "This payment has already been refunded in full."
    try:
        result = razorpay_api.refund_payment(pay.transaction_reference, rupee_paise,
                                             {"payment_number": pay.payment_number, "usd": str(amount)})
    except razorpay_api.RazorpayError as e:
        return f"Razorpay didn't make the refund ({e.code or 'error'}). Nothing was refunded."
    except httpx.HTTPError:
        return "We couldn't reach Razorpay. Nothing was refunded; please try again."
    _apply_refund(db, pay, _money(pay.refunded_amount) + amount, actor_id, result.get("id"))
    return None


def sync_razorpay(db: Session, pay: Payment) -> str | None:
    """Read the payment back from Razorpay and record it (for when a webhook was missed)."""
    if pay.gateway != "razorpay" or not pay.gateway_payment_id:
        return "This isn't a Razorpay payment."
    try:
        if pay.gateway_payment_id.startswith("order_"):
            attempts = razorpay_api.order_payments(pay.gateway_payment_id)
            order = {"captured": 0, "refunded": 0, "authorized": 1, "failed": 2}
            attempts.sort(key=lambda a: order.get(a.get("status", ""), 3))
            rp = attempts[0] if attempts else None
        else:
            rp = razorpay_api.get_payment(pay.gateway_payment_id)
    except (razorpay_api.RazorpayError, httpx.HTTPError):
        return "We couldn't reach Razorpay. Please try again."
    if rp:
        _apply_razorpay_payment(db, pay, rp)
    return None


# Smart Collect: bank transfer (NEFT / RTGS / IMPS) to the client's own account number

def bank_account_for(db: Session, client_id: str) -> VirtualAccount | None:
    return db.query(VirtualAccount).filter(VirtualAccount.gateway == "razorpay", VirtualAccount.client_id == client_id).first()


def open_bank_account(db: Session, client_id: str, client_name: str) -> str | None:
    """Give the client their own account number to transfer rupees to. Returns an error or None."""
    if not razorpay_api.smart_collect_enabled() or inr_rate(db) is None:
        return "Bank transfer isn't available right now. Please choose another way to pay."
    if bank_account_for(db, client_id):
        return None
    try:
        account = razorpay_api.create_virtual_account(client_id=client_id, client_name=client_name,
                                                      description=f"{settings.APP_NAME} payments from {client_name}")
    except (razorpay_api.RazorpayError, httpx.HTTPError):
        return "We couldn't set up your bank transfer details right now. Please try again in a few minutes."
    bank = razorpay_api.bank_receiver(account)
    if not bank.get("account_number"):
        return "We couldn't set up your bank transfer details right now. Please contact your tax team."
    db.add(VirtualAccount(gateway="razorpay", client_id=client_id, gateway_account_id=account["id"],
                          account_number=bank["account_number"], ifsc=bank.get("ifsc", ""),
                          bank_name=bank.get("bank_name"), account_name=bank.get("name")))
    db.add(AuditLog(actor_account_id=None, action="bank_transfer_account_opened", entity_type="client", entity_id=client_id,
                    old_values=None, new_values={"virtual_account": account["id"]}))
    try:
        db.commit()
    except IntegrityError:  # opened twice at once; keep the first
        db.rollback()
    return None


def _record_bank_transfer(db: Session, va: VirtualAccount, rp: dict, utr: str | None) -> Payment:
    """Rupees arrived in a client's Smart Collect account. They count towards the client's oldest open invoice, or,
    with none, as an advance payment on their latest filing, at today's rate. Commits."""
    rate = inr_rate(db)
    if rate is None:
        raise RuntimeError("No USD -> INR rate set; the webhook will be retried once an admin sets it.")
    rupees = (Decimal(rp.get("amount") or 0) / 100).quantize(CENT)
    usd = max((rupees / rate).quantize(CENT, rounding=ROUND_HALF_UP), CENT)
    inv = (db.query(Invoice).filter(Invoice.client_id == va.client_id, Invoice.status.in_(OPEN))
           .order_by(Invoice.invoice_date, Invoice.created_at).first())
    filing = None if inv else (db.query(TaxFiling).filter(TaxFiling.client_id == va.client_id)
                               .order_by(TaxFiling.tax_year.desc(), TaxFiling.created_at.desc()).first())
    pay = Payment(invoice_id=inv.id if inv else None, case_id=inv.case_id if inv else (filing.id if filing else None),
                  client_id=va.client_id, amount=usd, payment_method="bank_transfer", status="pending", gateway="razorpay",
                  gateway_payment_id=rp["id"][:100], transaction_reference=rp["id"][:100],
                  original_amount=rupees, original_currency="INR",
                  fee_amount=(Decimal(rp["fee"]) / 100).quantize(CENT) if rp.get("fee") is not None else None)
    db.add(pay)
    db.flush()
    _record_success(db, pay, "razorpay_bank_transfer_received",
                    {"razorpay_payment": rp["id"], "utr": utr, "rate": str(rate), "virtual_account": va.gateway_account_id})
    return pay


def _razorpay_entity(payload: dict, name: str) -> dict:
    return ((payload or {}).get(name) or {}).get("entity") or {}


def _by_razorpay_payment(db: Session, rp: dict) -> Payment | None:
    if not rp.get("id"):
        return None
    pay = db.query(Payment).filter(Payment.gateway == "razorpay", Payment.transaction_reference == rp["id"]).first()
    if not pay and rp.get("order_id"):
        pay = db.query(Payment).filter(Payment.gateway == "razorpay", Payment.gateway_payment_id == rp["order_id"]).first()
    return pay


def handle_razorpay_event(db: Session, event: dict, event_id: str) -> str:
    """Apply one signed Razorpay webhook (already checked by razorpay_api.verify_event). Safe to receive twice.
    Returns "applied", "ignored" or "duplicate"."""
    kind = str(event.get("event") or "")
    payload = event.get("payload") or {}
    event_id = (event_id or "")[:100]
    if not event_id:
        return "ignored"
    if db.query(PaymentEvent).filter(PaymentEvent.gateway == "razorpay", PaymentEvent.event_id == event_id).first():
        return "duplicate"
    rp = _razorpay_entity(payload, "payment")
    pay = None
    if kind == "virtual_account.credited":
        va_id = _razorpay_entity(payload, "virtual_account").get("id")
        va = db.query(VirtualAccount).filter(VirtualAccount.gateway_account_id == va_id).first() if va_id else None
        pay = _by_razorpay_payment(db, rp)
        if va and rp.get("id") and not pay:
            pay = _record_bank_transfer(db, va, rp, _razorpay_entity(payload, "bank_transfer").get("utr"))
    elif kind in ("payment.captured", "payment.authorized", "payment.failed", "order.paid"):
        pay = _by_razorpay_payment(db, rp)
        if pay and pay.payment_method != "bank_transfer":
            _apply_razorpay_payment(db, pay, rp)
    elif kind in ("refund.processed", "refund.created"):
        pay = _by_razorpay_payment(db, rp)
        if pay and rp.get("amount_refunded") is not None:
            _apply_refund(db, pay, _usd_share(pay, int(rp["amount_refunded"])), None, _razorpay_entity(payload, "refund").get("id"))
    elif kind.startswith("payment.dispute."):
        pay = _by_razorpay_payment(db, rp)
        dispute = _razorpay_entity(payload, "dispute")
        if pay:
            _apply_dispute(db, pay, {"id": dispute.get("id"), "reason": dispute.get("reason_code"),
                                     "status": dispute.get("status") or kind.rsplit(".", 1)[-1]})
    db.add(PaymentEvent(gateway="razorpay", event_id=event_id, event_type=kind[:100], payment_id=pay.id if pay else None))
    try:
        db.commit()
    except IntegrityError:
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
