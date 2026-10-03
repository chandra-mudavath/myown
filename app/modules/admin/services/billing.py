"""Admin Payments: invoices built from the service price list, and payments received offline.

Record-only: nothing is charged online and the case workflow is not affected.
Statuses follow the database checks: invoices draft / issued / partially_paid / paid / void,
payments succeeded (recorded here) / pending / failed / refunded.
Money is Decimal, rounded to cents. Every change is written to audit_logs.
"""
from datetime import date, datetime, time, timezone
from decimal import Decimal, InvalidOperation

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.modules.client.models.client import Client
from app.platform.models.billing import CaseService, Invoice, Payment, Service
from app.platform.models.system import AuditLog
from app.platform.models.tax_filing import TaxFiling

CENT = Decimal("0.01")
METHODS = [("bank_transfer", "Bank transfer"), ("card", "Card"), ("cash", "Cash"), ("check", "Check"), ("other", "Other")]
METHOD_LABELS = dict(METHODS)
OPEN = ("issued", "partially_paid")
STATUS_LABELS = {"draft": "Draft", "issued": "Issued", "partially_paid": "Partly paid", "paid": "Paid", "void": "Void"}


def _money(value) -> Decimal:
    return Decimal(value or 0).quantize(CENT)


def _parse_money(value: str | None, what: str, allow_zero: bool = True) -> tuple[Decimal | None, str | None]:
    raw = str(value or "").replace(",", "").replace("$", "").strip() or "0"
    try:
        amount = Decimal(raw)
    except InvalidOperation:
        return None, f"{what} must be a number, e.g. 150 or 149.99."
    if amount < 0 or (not allow_zero and amount == 0):
        return None, f"{what} must be {'zero or more' if allow_zero else 'more than zero'}."
    if amount > Decimal("10000000"):
        return None, f"{what} is too large."
    return amount.quantize(CENT), None


def _parse_date(value: str | None, what: str, required: bool = True) -> tuple[date | None, str | None]:
    if not value:
        return (None, f"{what} is required.") if required else (None, None)
    try:
        return date.fromisoformat(value), None
    except ValueError:
        return None, f"{what} must be a date."


def _audit(db: Session, actor_id: str, action: str, entity_type: str, entity_id: str, old: dict | None, new: dict | None) -> None:
    db.add(AuditLog(actor_account_id=actor_id, action=action, entity_type=entity_type, entity_id=entity_id,
                    old_values=old, new_values=new))


def paid_amount(db: Session, invoice_id: str) -> Decimal:
    total = db.query(func.coalesce(func.sum(Payment.amount), 0)).filter(
        Payment.invoice_id == invoice_id, Payment.status == "succeeded").scalar()
    return _money(total)


def is_overdue(inv: Invoice, today: date | None = None) -> bool:
    today = today or datetime.now(timezone.utc).date()
    return inv.status in OPEN and inv.due_date is not None and inv.due_date < today


# ── Creating and changing invoices ────────────────────────────────────────────

def create_invoice(db: Session, actor_id: str, case_id: str, quantities: dict[str, str], discount: str, tax: str,
                   invoice_date: str, due_date: str) -> tuple[Invoice | None, str | None]:
    """Create a draft invoice for a case from {service_id: quantity}. Returns (invoice, error)."""
    filing = db.get(TaxFiling, case_id) if case_id else None
    if not filing:
        return None, "Choose the case to invoice."
    lines = []
    for service_id, qty in quantities.items():
        if not str(qty or "").strip() or str(qty).strip() == "0":
            continue
        try:
            n = int(qty)
        except ValueError:
            return None, "Quantities must be whole numbers."
        if not 1 <= n <= 100:
            return None, "Quantities must be between 1 and 100."
        svc = db.get(Service, service_id)
        if not svc or not svc.is_active:
            return None, "One of the selected services is no longer available."
        lines.append((svc, n))
    if not lines:
        return None, "Add at least one service (set its quantity to 1 or more)."
    disc, err = _parse_money(discount, "Discount")
    if err:
        return None, err
    tax_amt, err = _parse_money(tax, "Tax")
    if err:
        return None, err
    inv_date, err = _parse_date(invoice_date, "Invoice date")
    if err:
        return None, err
    due, err = _parse_date(due_date, "Due date", required=False)
    if err:
        return None, err
    if due and due < inv_date:
        return None, "The due date can't be before the invoice date."
    subtotal = sum((_money(svc.base_price) * n for svc, n in lines), Decimal("0")).quantize(CENT)
    if disc > subtotal:
        return None, "The discount can't be more than the subtotal."
    total = (subtotal - disc + tax_amt).quantize(CENT)

    inv = Invoice(client_id=filing.client_id, case_id=filing.id, subtotal=subtotal, discount_amount=disc,
                  tax_amount=tax_amt, total_amount=total, status="draft", invoice_date=inv_date, due_date=due)
    db.add(inv)
    db.flush()  # invoice_number is filled in on insert
    for svc, n in lines:
        unit = _money(svc.base_price)  # copied, so later price changes don't alter this invoice
        db.add(CaseService(case_id=filing.id, invoice_id=inv.id, service_id=svc.id, quantity=n,
                           unit_price=unit, total_amount=(unit * n).quantize(CENT)))
    _audit(db, actor_id, "invoice_created", "invoice", inv.id, None,
           {"invoice_number": inv.invoice_number, "case": filing.case_number, "total": str(total)})
    db.commit()
    return inv, None


def issue_invoice(db: Session, actor_id: str, inv: Invoice) -> str | None:
    if inv.status != "draft":
        return "Only a draft invoice can be issued."
    inv.status = "issued"
    _audit(db, actor_id, "invoice_issued", "invoice", inv.id, {"status": "draft"}, {"status": "issued"})
    db.commit()
    return None


def void_invoice(db: Session, actor_id: str, inv: Invoice) -> str | None:
    if inv.status == "void":
        return "This invoice is already void."
    if paid_amount(db, inv.id) > 0:
        return "An invoice with payments can't be voided."
    old = inv.status
    inv.status = "void"
    _audit(db, actor_id, "invoice_voided", "invoice", inv.id, {"status": old}, {"status": "void"})
    db.commit()
    return None


def record_payment(db: Session, actor_id: str, inv: Invoice, amount: str, method: str, reference: str, paid_on: str) -> str | None:
    """Record a payment received offline. Issues a draft invoice; marks it paid when the balance reaches zero."""
    if inv.status in ("void", "paid"):
        return f"This invoice is {STATUS_LABELS[inv.status].lower()}; no more payments can be recorded."
    amt, err = _parse_money(amount, "Amount", allow_zero=False)
    if err:
        return err
    balance = _money(inv.total_amount) - paid_amount(db, inv.id)
    if amt > balance:
        return f"That's more than the balance due (${balance:,.2f})."
    if method not in METHOD_LABELS:
        return "Choose how the payment was made."
    day, err = _parse_date(paid_on, "Payment date")
    if err:
        return err
    if day > datetime.now(timezone.utc).date():
        return "The payment date can't be in the future."
    ref = " ".join((reference or "").split())[:100] or None

    pay = Payment(invoice_id=inv.id, client_id=inv.client_id, amount=amt, payment_method=method,
                  transaction_reference=ref, status="succeeded", paid_at=datetime.combine(day, time(12), tzinfo=timezone.utc))
    db.add(pay)
    db.flush()
    old = inv.status
    inv.status = "paid" if amt == balance else "partially_paid"
    _audit(db, actor_id, "payment_recorded", "payment", pay.id, {"invoice_status": old},
           {"payment_number": pay.payment_number, "invoice": inv.invoice_number, "amount": str(amt), "method": method,
            "invoice_status": inv.status})
    db.commit()
    return None


# ── Reading ───────────────────────────────────────────────────────────────────

def summary(db: Session) -> dict:
    today = datetime.now(timezone.utc).date()
    open_invoices = db.query(Invoice).filter(Invoice.status.in_(OPEN)).all()
    paid = _paid_map(db, [i.id for i in open_invoices])
    outstanding = sum((_money(i.total_amount) - paid.get(i.id, Decimal("0")) for i in open_invoices), Decimal("0"))
    overdue = [i for i in open_invoices if is_overdue(i, today)]
    month_start = datetime.combine(today.replace(day=1), time(0))
    collected = db.query(func.coalesce(func.sum(Payment.amount), 0)).filter(
        Payment.status == "succeeded", Payment.paid_at >= month_start).scalar()
    return {
        "outstanding": outstanding.quantize(CENT), "open_count": len(open_invoices),
        "overdue": sum((_money(i.total_amount) - paid.get(i.id, Decimal("0")) for i in overdue), Decimal("0")).quantize(CENT),
        "overdue_count": len(overdue), "collected_month": _money(collected), "month": today.strftime("%B %Y"),
        "draft_count": db.query(Invoice).filter(Invoice.status == "draft").count(),
    }


def _paid_map(db: Session, invoice_ids: list[str]) -> dict[str, Decimal]:
    if not invoice_ids:
        return {}
    rows = db.query(Payment.invoice_id, func.sum(Payment.amount)).filter(
        Payment.invoice_id.in_(invoice_ids), Payment.status == "succeeded").group_by(Payment.invoice_id)
    return {i: _money(a) for i, a in rows}


def list_invoices(db: Session, status: str = "", q: str = "", case_id: str = "") -> list[dict]:
    today = datetime.now(timezone.utc).date()
    query = db.query(Invoice, TaxFiling).join(TaxFiling, TaxFiling.id == Invoice.case_id)
    if status == "overdue":
        query = query.filter(Invoice.status.in_(OPEN), Invoice.due_date < today)
    elif status in STATUS_LABELS:
        query = query.filter(Invoice.status == status)
    if case_id:
        query = query.filter(Invoice.case_id == case_id)
    if q.strip():
        term = f"%{q.strip()}%"
        query = query.filter(or_(Invoice.invoice_number.ilike(term), TaxFiling.case_number.ilike(term),
                                 TaxFiling.first_name.ilike(term), TaxFiling.last_name.ilike(term),
                                 (TaxFiling.first_name + " " + TaxFiling.last_name).ilike(term)))
    rows = query.order_by(Invoice.invoice_date.desc(), Invoice.created_at.desc()).all()
    paid = _paid_map(db, [i.id for i, _ in rows])
    out = []
    for inv, f in rows:
        p = paid.get(inv.id, Decimal("0"))
        out.append({"invoice": inv, "filing": f, "client_name": f"{f.first_name} {f.last_name}", "paid": p,
                    "balance": (_money(inv.total_amount) - p) if inv.status != "void" else Decimal("0"),
                    "overdue": is_overdue(inv, today), "status_label": STATUS_LABELS.get(inv.status, inv.status)})
    return out


def invoice_detail(db: Session, invoice_id: str) -> dict | None:
    inv = db.get(Invoice, invoice_id)
    if not inv:
        return None
    filing = db.get(TaxFiling, inv.case_id)
    client = db.get(Client, inv.client_id)
    lines = db.query(CaseService).filter(CaseService.invoice_id == inv.id).order_by(CaseService.created_at).all()
    payments = db.query(Payment).filter(Payment.invoice_id == inv.id).order_by(Payment.paid_at, Payment.created_at, Payment.payment_number).all()
    paid = paid_amount(db, inv.id)
    return {"invoice": inv, "filing": filing, "client": client, "lines": lines, "payments": payments, "paid": paid,
            "balance": (_money(inv.total_amount) - paid) if inv.status != "void" else Decimal("0"),
            "overdue": is_overdue(inv), "status_label": STATUS_LABELS.get(inv.status, inv.status)}


def form_options(db: Session) -> dict:
    cases = (db.query(TaxFiling).order_by(TaxFiling.created_at.desc()).all())
    return {"cases": cases, "services": db.query(Service).filter(Service.is_active.is_(True)).order_by(Service.name).all()}
