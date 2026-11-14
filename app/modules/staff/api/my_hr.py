"""Staff self-service for HR: check in and out, apply for and cancel leave, and see their own
finalised payslips and letters. Everything uses the HR services, so the rules are the same as
in the HR workspace. Staff only ever see their own records, and only final documents.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import StaffAccount
from app.core.templates import templates
from app.modules.hr.api.common import pdf_download, redirect
from app.modules.hr.models.records import HrDocument, LeaveRequest
from app.modules.hr.services import documents, hr_settings, notify, time_off
from app.modules.staff.api.staff import _nav_context
from app.modules.staff.models.staff import Staff

router = APIRouter(prefix="/staff/hr", tags=["staff"])


def _me(db: Session, account) -> Staff:
    staff = db.query(Staff).filter(Staff.account_id == account.id).first()
    if not staff:
        raise HTTPException(status_code=404, detail="Staff account not found")
    return staff


def _ctx(request: Request, db: Session, account, staff: Staff, active_nav: str, **extra) -> dict:
    hr_settings.ensure_defaults(db)
    return {"request": request, "account": account, "staff": staff, "app_name": settings.APP_NAME, "active_nav": active_nav,
            **_nav_context(db, staff), **extra}


# ── Attendance ────────────────────────────────────────────────────────────

@router.get("/attendance", response_class=HTMLResponse, name="staff_my_attendance")
def my_attendance(request: Request, account: StaffAccount, db: Session = Depends(get_db), month: str = "",
                  saved: str = "", error: str = ""):
    staff = _me(db, account)
    y, m = time_off.parse_month(month)
    codes = time_off.month_codes(db, staff, y, m)
    today = time_off.today()
    today_code = time_off.month_codes(db, staff, today.year, today.month)[today.day - 1]
    row = time_off.today_row(db, staff)
    return templates.TemplateResponse(request, "staff/hr/attendance.html", _ctx(
        request, db, account, staff, "my_attendance", nav=time_off.month_nav(y, m), codes=codes, totals=time_off.totals(codes),
        code_labels=time_off.CODE_LABELS, row=row, today=today, today_code=today_code,
        checked_in_at=time_off.local_time(row.check_in_at) if row and row.check_in_at else "",
        saved=saved, error=error))


@router.post("/check-in")
def my_check_in(account: StaffAccount, db: Session = Depends(get_db)):
    err = time_off.check_in(db, _me(db, account))
    return redirect("/staff/hr/attendance", error=err, saved=None if err else "Checked in. Have a good day.")


@router.post("/check-out")
def my_check_out(account: StaffAccount, db: Session = Depends(get_db)):
    err = time_off.check_out(db, _me(db, account))
    return redirect("/staff/hr/attendance", error=err, saved=None if err else "Checked out. See you tomorrow.")


# ── Leaves ────────────────────────────────────────────────────────────────

@router.get("/leaves", response_class=HTMLResponse, name="staff_my_leaves")
def my_leaves(request: Request, account: StaffAccount, db: Session = Depends(get_db), saved: str = "", error: str = ""):
    staff = _me(db, account)
    return templates.TemplateResponse(request, "staff/hr/leaves.html", _ctx(
        request, db, account, staff, "my_leaves", balances=time_off.balances(db, staff),
        leave_requests=time_off.requests(db, staff), leave_types=hr_settings.leave_types(db, active_only=True),
        holidays=[h for h in time_off.holidays(db, time_off.today().year) if h.day >= time_off.today()],
        today=time_off.today(), saved=saved, error=error))


@router.post("/leaves")
def my_apply_leave(request: Request, account: StaffAccount, db: Session = Depends(get_db), leave_type_id: str = Form(""),
                   start_date: str = Form(""), end_date: str = Form(""), half_day: str = Form(""), reason: str = Form("")):
    staff = _me(db, account)
    req, err = time_off.apply(db, account.id, staff, leave_type_id, start_date, end_date, bool(half_day), reason)
    if req:
        notify.leave_requested(db, notify.base_url(request), req)
    return redirect("/staff/hr/leaves", error=err, saved=None if err else "Leave request sent to HR.")


@router.post("/leaves/{request_id}/cancel")
def my_cancel_leave(request: Request, request_id: str, account: StaffAccount, db: Session = Depends(get_db)):
    staff = _me(db, account)
    req = db.get(LeaveRequest, request_id)
    if not req or req.staff_id != staff.id:
        raise HTTPException(status_code=404, detail="Leave request not found")
    was = req.status
    err = time_off.cancel(db, account.id, req)
    if not err and was == "approved":
        notify.leave_cancelled(db, notify.base_url(request), req)
    return redirect("/staff/hr/leaves", error=err, saved=None if err else "Leave cancelled.")


# ── Payslips and letters ──────────────────────────────────────────────────

@router.get("/payslips", response_class=HTMLResponse, name="staff_my_payslips")
def my_payslips(request: Request, account: StaffAccount, db: Session = Depends(get_db)):
    staff = _me(db, account)
    return templates.TemplateResponse(request, "staff/hr/documents.html", _ctx(
        request, db, account, staff, "my_payslips", heading="My payslips",
        docs=documents.for_staff(db, staff, final_only=True, kinds=("PAYSLIP",)), title=documents.title, money=documents.money,
        empty="Your payslips will appear here once HR finalises them."))


@router.get("/documents", response_class=HTMLResponse, name="staff_my_documents")
def my_documents(request: Request, account: StaffAccount, db: Session = Depends(get_db)):
    staff = _me(db, account)
    letters = [d for d in documents.for_staff(db, staff, final_only=True) if d.kind != "PAYSLIP"]
    return templates.TemplateResponse(request, "staff/hr/documents.html", _ctx(
        request, db, account, staff, "my_documents", heading="My documents", docs=letters, title=documents.title,
        money=documents.money, empty="Letters from HR (appointment, salary revision, confirmation) will appear here."))


def _my_doc(db: Session, account, doc_id: str) -> HrDocument:
    staff = _me(db, account)
    doc = db.get(HrDocument, doc_id)
    if not doc or doc.staff_id != staff.id or doc.status != "final":  # own, final documents only
        raise HTTPException(status_code=404, detail="Document not found")
    return doc


@router.get("/documents/{doc_id}", response_class=HTMLResponse, name="staff_my_document")
def my_document(request: Request, doc_id: str, account: StaffAccount, db: Session = Depends(get_db)):
    doc = _my_doc(db, account, doc_id)
    return templates.TemplateResponse(request, "staff/hr/document.html", _ctx(
        request, db, account, _me(db, account), "my_payslips" if doc.kind == "PAYSLIP" else "my_documents",
        doc=doc, title=documents.title(doc), letter=documents.render(db, doc)))


@router.get("/documents/{doc_id}/print", response_class=HTMLResponse)
def my_document_print(request: Request, doc_id: str, account: StaffAccount, db: Session = Depends(get_db)):
    doc = _my_doc(db, account, doc_id)
    return templates.TemplateResponse(request, "hr/letters/print.html", {
        "request": request, "letter": documents.render(db, doc), "title": documents.title(doc), "doc": doc})


@router.get("/documents/{doc_id}/pdf")
def my_document_pdf(doc_id: str, account: StaffAccount, db: Session = Depends(get_db)):
    doc = _my_doc(db, account, doc_id)
    return pdf_download(db, [doc], f"/staff/hr/documents/{doc_id}/print")
