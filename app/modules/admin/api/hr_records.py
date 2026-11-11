"""Admin console: HR records, read-only (decided Nov 2026). Admins see the same employee record HR
keeps (job, salary, files, letters, attendance, leaves, payslips) but change nothing here."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import AdminAccount
from app.core.templates import templates
from app.modules.admin.models.admin import Admin
from app.modules.hr.models.records import HrDocument
from app.modules.hr.services import documents, employee_view, hr_settings, people, staff_files, time_off
from app.modules.staff.models.staff_records import StaffDocument

router = APIRouter(prefix="/admin/hr-records", tags=["admin"])


def _ctx(request: Request, db: Session, account, **extra) -> dict:
    hr_settings.ensure_defaults(db)
    return {"request": request, "account": account, "admin": db.query(Admin).filter(Admin.account_id == account.id).first(),
            "app_name": settings.APP_NAME, "active_nav": "hr_records", **extra}


@router.get("", response_class=HTMLResponse, name="admin_hr_records")
def hr_records(request: Request, account: AdminAccount, db: Session = Depends(get_db), q: str = "", month: str = ""):
    y, m = time_off.parse_month(month)
    return templates.TemplateResponse(request, "admin/hr_records/index.html", _ctx(
        request, db, account, stats=people.dashboard_stats(db), employees=people.employee_rows(db, q), q=q,
        nav=time_off.month_nav(y, m), payslip_docs=documents.payslips(db, documents.period_of(y, m)),
        leave_requests=time_off.requests(db, status="pending"), money=documents.money, people=people))


@router.get("/{staff_id}", response_class=HTMLResponse, name="admin_hr_record")
def hr_record(request: Request, staff_id: str, account: AdminAccount, db: Session = Depends(get_db), tab: str = "summary",
              month: str = ""):
    staff = people.employee(db, staff_id)
    if not staff:
        raise HTTPException(status_code=404, detail="Employee not found")
    return templates.TemplateResponse(request, "admin/hr_records/employee.html", _ctx(
        request, db, account, editable=False, base="/admin/hr-records", page=f"/admin/hr-records/{staff_id}",
        **employee_view.context(db, staff, tab, month)))


def _doc_or_404(db: Session, doc_id: str) -> HrDocument:
    doc = db.get(HrDocument, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    return doc


@router.get("/documents/{doc_id}", response_class=HTMLResponse, name="admin_hr_document")
def hr_record_document(request: Request, doc_id: str, account: AdminAccount, db: Session = Depends(get_db)):
    doc = _doc_or_404(db, doc_id)
    return templates.TemplateResponse(request, "admin/hr_records/document.html", _ctx(
        request, db, account, doc=doc, title=documents.title(doc), letter=documents.render(db, doc)))


@router.get("/documents/{doc_id}/print", response_class=HTMLResponse)
def hr_record_document_print(request: Request, doc_id: str, account: AdminAccount, db: Session = Depends(get_db)):
    doc = _doc_or_404(db, doc_id)
    return templates.TemplateResponse(request, "hr/letters/print.html", {
        "request": request, "letter": documents.render(db, doc), "title": documents.title(doc), "doc": doc})


@router.get("/files/{file_id}")
def hr_record_file(request: Request, file_id: str, account: AdminAccount, db: Session = Depends(get_db), inline: bool = False):
    doc = db.get(StaffDocument, file_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    return staff_files.download(db, request, account.id, doc, inline)
