"""Admin Payments: invoice list and totals, new invoice, invoice detail, issue / void, record a payment."""
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.files import send_file
from app.core.deps import AdminAccount
from app.core.templates import templates
from app.modules.admin.services import billing
from app.modules.admin.services.profile_service import current_admin
from app.platform.models.billing import Invoice, Payment
from app.platform.services import access_log, payments as client_payments

router = APIRouter(prefix="/admin/payments", tags=["admin"])

FILTERS = [("", "All"), ("awaiting", "Awaiting verification"), ("draft", "Draft"), ("issued", "Issued"), ("partially_paid", "Partly paid"),
           ("overdue", "Overdue"), ("paid", "Paid"), ("void", "Void")]


def _ctx(request: Request, account, db: Session, **extra) -> dict:
    return {"request": request, "account": account, "admin": current_admin(db, account), "app_name": settings.APP_NAME,
            "active_nav": "payments", **extra}


def _to_invoice(invoice_id: str, err: str | None, saved: str) -> RedirectResponse:
    return RedirectResponse(f"/admin/payments/invoices/{invoice_id}?{urlencode({'error': err} if err else {'saved': saved})}", status_code=303)


def _invoice_or_404(db: Session, invoice_id: str) -> Invoice:
    inv = db.get(Invoice, invoice_id)
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found.")
    return inv


@router.get("", response_class=HTMLResponse, name="admin_payments")
def payments_home(request: Request, account: AdminAccount, db: Session = Depends(get_db), status: str = "", q: str = ""):
    return templates.TemplateResponse(request, "admin/payments/index.html", _ctx(
        request, account, db, summary=billing.summary(db), invoices=billing.list_invoices(db, status, q),
        pending=client_payments.pending_payments(db),
        filters=FILTERS, status=status, q=q))


@router.get("/invoices/new", response_class=HTMLResponse, name="admin_invoice_new")
def invoice_new(request: Request, account: AdminAccount, db: Session = Depends(get_db), case_id: str = "", error: str = ""):
    today = datetime.now(timezone.utc).date()
    return templates.TemplateResponse(request, "admin/payments/new.html", _ctx(
        request, account, db, **billing.form_options(db), case_id=case_id, error=error,
        today=today.isoformat(), due=(today + timedelta(days=14)).isoformat(), form={}))


@router.post("/invoices")
async def invoice_create(request: Request, account: AdminAccount, db: Session = Depends(get_db)):
    form = await request.form()
    quantities = {k[4:]: str(v) for k, v in form.items() if k.startswith("qty_")}
    inv, err = billing.create_invoice(db, account.id, str(form.get("case_id", "")), quantities, str(form.get("discount", "")),
                                      str(form.get("tax", "")), str(form.get("invoice_date", "")), str(form.get("due_date", "")))
    if err:
        # show the form again with what was entered
        return templates.TemplateResponse(request, "admin/payments/new.html", _ctx(
            request, account, db, **billing.form_options(db), case_id=str(form.get("case_id", "")), error=err,
            today=str(form.get("invoice_date", "")), due=str(form.get("due_date", "")), form=dict(form)), status_code=422)
    return _to_invoice(inv.id, None, "Invoice created as a draft.")


@router.get("/invoices/{invoice_id}", response_class=HTMLResponse, name="admin_invoice")
def invoice_view(invoice_id: str, request: Request, account: AdminAccount, db: Session = Depends(get_db),
                 saved: str = "", error: str = ""):
    detail = billing.invoice_detail(db, invoice_id)
    if not detail:
        raise HTTPException(status_code=404, detail="Invoice not found.")
    return templates.TemplateResponse(request, "admin/payments/invoice.html", _ctx(
        request, account, db, **detail, methods=billing.METHODS, method_labels=billing.METHOD_LABELS,
        today=datetime.now(timezone.utc).date().isoformat(), saved=saved, error=error))


@router.post("/invoices/{invoice_id}/issue")
def invoice_issue(invoice_id: str, account: AdminAccount, db: Session = Depends(get_db)):
    inv = _invoice_or_404(db, invoice_id)
    return _to_invoice(inv.id, billing.issue_invoice(db, account.id, inv), "Invoice issued.")


@router.post("/invoices/{invoice_id}/void")
def invoice_void(invoice_id: str, account: AdminAccount, db: Session = Depends(get_db)):
    inv = _invoice_or_404(db, invoice_id)
    return _to_invoice(inv.id, billing.void_invoice(db, account.id, inv), "Invoice voided.")


@router.post("/invoices/{invoice_id}/payments")
def invoice_payment(invoice_id: str, account: AdminAccount, db: Session = Depends(get_db),
                    amount: str = Form(""), payment_method: str = Form(""), transaction_reference: str = Form(""),
                    paid_on: str = Form("")):
    inv = _invoice_or_404(db, invoice_id)
    err = billing.record_payment(db, account.id, inv, amount, payment_method, transaction_reference, paid_on)
    return _to_invoice(inv.id, err, "Payment recorded.")


# ── Payments submitted by clients (UPI / bank transfer) ──────────────────────

def _payment_or_404(db: Session, payment_id: str) -> Payment:
    pay = db.get(Payment, payment_id)
    if not pay:
        raise HTTPException(status_code=404, detail="Payment not found.")
    return pay


@router.post("/payments/{payment_id}/verify")
def payment_verify(payment_id: str, account: AdminAccount, db: Session = Depends(get_db)):
    pay = _payment_or_404(db, payment_id)
    return _to_invoice(pay.invoice_id, client_payments.verify(db, account.id, pay), "Payment verified.")


@router.post("/payments/{payment_id}/reject")
def payment_reject(payment_id: str, account: AdminAccount, db: Session = Depends(get_db), reason: str = Form("")):
    pay = _payment_or_404(db, payment_id)
    return _to_invoice(pay.invoice_id, client_payments.reject(db, account.id, pay, reason), "Payment rejected; the client can see why.")


@router.get("/payments/{payment_id}/proof", name="admin_payment_proof")
def payment_proof(payment_id: str, request: Request, account: AdminAccount, db: Session = Depends(get_db)):
    pay = _payment_or_404(db, payment_id)
    if not pay.proof_path:
        raise HTTPException(status_code=404, detail="No proof was attached.")
    access_log.record(db, request, account.id, "payment_proof_viewed", "payment", pay.id)
    return send_file(pay.proof_path, pay.proof_filename, pay.proof_mime, inline=True)
