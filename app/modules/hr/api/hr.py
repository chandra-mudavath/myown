"""HR workspace: dashboard (statistics, then every employee), one page per employee with its tabs in the
sidebar, new staff accounts, HR files, and HR's own profile. Attendance, leaves and settings are in
time_off.py; payslips, offers and letters in documents.py; Messages with the other chat pages.

HR works on staff only. Nothing here reads clients, cases or client documents.
"""
from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import HrAccount
from app.core.templates import templates
from app.modules.admin.services import accounts as account_controls
from app.modules.hr.api.common import employee_or_404, hr_ctx, redirect
from app.modules.hr.services import documents, employee_view, notify, people, staff_files, time_off
from app.modules.staff.models.staff_records import StaffDocument
from app.platform.models.auth import AuthAccount
from app.platform.services import team_accounts
from app.platform.services.auth_service import change_password
from app.platform.services.catalog import staff_roles

router = APIRouter(prefix="/hr", tags=["hr"])


@router.get("", include_in_schema=False)
@router.get("/", include_in_schema=False)
def hr_home():
    return RedirectResponse("/hr/dashboard", status_code=303)


@router.get("/dashboard", response_class=HTMLResponse, name="hr_dashboard")
def hr_dashboard(request: Request, account: HrAccount, db: Session = Depends(get_db), q: str = "", status: str = "",
                 saved: str = "", error: str = ""):
    active = people.staff_query(db).filter(AuthAccount.is_active.is_(True)).all()
    stats = people.dashboard_stats(db)
    stats.update(on_leave_today=time_off.on_leave_today(db, active), not_checked_in=len(time_off.not_checked_in_today(db, active)))
    return templates.TemplateResponse(request, "hr/dashboard.html", hr_ctx(
        request, db, account, active_nav="dashboard", stats=stats,
        employees=people.employee_rows(db, q, status), q=q, status=status, saved=saved, error=error))


# ── New staff account ─────────────────────────────────────────────────────

def _new_employee_page(request, db, account, form, error="", created=None, status_code=200):
    return templates.TemplateResponse(request, "hr/new_employee.html", hr_ctx(
        request, db, account, active_nav="dashboard", roles=staff_roles(db), form=form, error=error, created=created),
        status_code=status_code)


@router.get("/employees/new", response_class=HTMLResponse, name="hr_new_employee")
def hr_new_employee(request: Request, account: HrAccount, db: Session = Depends(get_db), offer: str = ""):
    form = {}
    doc = documents.get(db, offer) if offer else None
    if doc and doc.kind == "OFFER" and doc.offer_status == "accepted":  # from an accepted offer
        name = doc.data.get("candidate_name", "").split()
        form = {"first_name": name[0] if name else "", "last_name": " ".join(name[1:]), "offer": doc.id}
    return _new_employee_page(request, db, account, form)


@router.post("/employees", response_class=HTMLResponse)
def hr_create_employee(request: Request, account: HrAccount, db: Session = Depends(get_db),
                       first_name: str = Form(""), last_name: str = Form(""), email: str = Form(""),
                       phone: str = Form(""), role: str = Form(""), offer: str = Form("")):
    form = {"first_name": first_name, "last_name": last_name, "email": email, "phone": phone, "role": role, "offer": offer}
    try:
        # HR makes staff accounts only; HR accounts come from an admin
        acc, staff, password = team_accounts.create(db, account.id, first_name, last_name, email, phone, role, allow_hr=False)
    except team_accounts.AccountError as e:
        return _new_employee_page(request, db, account, form, error=str(e), status_code=400)
    doc = documents.get(db, offer) if offer else None
    if doc and doc.kind == "OFFER" and doc.offer_status == "accepted" and not doc.staff_id:
        documents.link_offer_to_staff(db, account.id, doc, staff)
    hr = people.current_hr(db, account)
    notify.account_created(db, notify.base_url(request), staff.first_name, acc.email, staff.staff_number,
                           people.full_name(hr) if hr else "HR")
    # The temporary password is shown once, on this page: never in a URL, log or audit row
    response = _new_employee_page(request, db, account, {}, created={
        "name": people.full_name(staff), "email": acc.email, "number": staff.staff_number, "id": staff.id, "temp_password": password})
    response.headers["Cache-Control"] = "no-store"
    return response


# ── One employee ──────────────────────────────────────────────────────────

@router.get("/employees/{staff_id}", response_class=HTMLResponse, name="hr_employee")
def hr_employee(request: Request, staff_id: str, account: HrAccount, db: Session = Depends(get_db),
                tab: str = "summary", month: str = "", saved: str = "", error: str = ""):
    staff = employee_or_404(db, staff_id)
    return templates.TemplateResponse(request, "hr/employee.html", hr_ctx(
        request, db, account, active_nav="employees", editable=True, base="/hr", saved=saved, error=error,
        **employee_view.context(db, staff, tab, month)))


@router.post("/employees/{staff_id}/job")
async def hr_save_job(request: Request, staff_id: str, account: HrAccount, db: Session = Depends(get_db)):
    staff = employee_or_404(db, staff_id)
    form = {k: str(v) for k, v in (await request.form()).items()}
    err = people.save_job(db, account.id, staff, form)
    return redirect(f"/hr/employees/{staff_id}?tab=job", error=err, saved=None if err else "Job details saved.")


@router.post("/employees/{staff_id}/payroll")
async def hr_save_payroll(request: Request, staff_id: str, account: HrAccount, db: Session = Depends(get_db)):
    staff = employee_or_404(db, staff_id)
    form = {k: str(v) for k, v in (await request.form()).items()}
    err = people.save_payroll(db, account.id, staff, form)
    return redirect(f"/hr/employees/{staff_id}?tab=job#payroll", error=err, saved=None if err else "Payroll details saved.")


@router.post("/employees/{staff_id}/salary")
def hr_add_salary(staff_id: str, account: HrAccount, db: Session = Depends(get_db), new_salary: str = Form(""),
                  effective_date: str = Form(""), reason: str = Form("")):
    staff = employee_or_404(db, staff_id)
    err = people.add_salary_change(db, account.id, staff, new_salary, effective_date, reason)
    return redirect(f"/hr/employees/{staff_id}?tab=salary", error=err,
                    saved=None if err else "Salary change added. You can now make the salary revision letter from the Letters tab.")


def _set_active(request: Request, staff_id: str, account, db: Session, active: bool):
    staff = employee_or_404(db, staff_id)
    err = account_controls.set_active(db, account.id, staff.account, active)
    if not err:
        notify.account_active(db, notify.base_url(request), staff.first_name, staff.account.email, active)
    return redirect(f"/hr/employees/{staff_id}?tab=access", error=err,
                    saved=None if err else ("Account activated." if active else "Account deactivated. They can no longer sign in."))


@router.post("/employees/{staff_id}/deactivate")
def hr_deactivate(request: Request, staff_id: str, account: HrAccount, db: Session = Depends(get_db)):
    return _set_active(request, staff_id, account, db, False)


@router.post("/employees/{staff_id}/activate")
def hr_activate(request: Request, staff_id: str, account: HrAccount, db: Session = Depends(get_db)):
    return _set_active(request, staff_id, account, db, True)


# ── Documents on file ─────────────────────────────────────────────────────

@router.post("/employees/{staff_id}/files")
def hr_upload_file(staff_id: str, account: HrAccount, db: Session = Depends(get_db), file: UploadFile | None = File(None),
                   document_type: str = Form(""), expiry_date: str = Form(""), notes: str = Form("")):
    staff = employee_or_404(db, staff_id)
    err = staff_files.upload(db, account.id, staff, file, document_type, expiry_date, notes)
    return redirect(f"/hr/employees/{staff_id}?tab=files", error=err, saved=None if err else "Document uploaded.")


def _file_or_404(db: Session, file_id: str) -> StaffDocument:
    doc = db.get(StaffDocument, file_id)
    if not doc or not people.employee(db, doc.staff_id):
        raise HTTPException(status_code=404, detail="Document not found")
    return doc


@router.get("/files/{file_id}", name="hr_file")
def hr_file(request: Request, file_id: str, account: HrAccount, db: Session = Depends(get_db), inline: bool = False):
    return staff_files.download(db, request, account.id, _file_or_404(db, file_id), inline)


@router.post("/files/{file_id}/renewal")
def hr_ask_renewal(file_id: str, account: HrAccount, db: Session = Depends(get_db)):
    doc = _file_or_404(db, file_id)
    sent = notify.renewal_needed(db, doc.staff, doc.document_type.name, doc.expiry_date, time_off.today() + timedelta(days=14))
    return redirect(f"/hr/employees/{doc.staff_id}?tab=files",
                    saved="Renewal request emailed." if sent else None,
                    error=None if sent else "The renewal email is turned off in HR Settings.")


# ── My profile ────────────────────────────────────────────────────────────

@router.get("/profile", response_class=HTMLResponse, name="hr_profile")
def hr_profile(request: Request, account: HrAccount, db: Session = Depends(get_db), saved: str = "", error: str = ""):
    return templates.TemplateResponse(request, "hr/profile.html", hr_ctx(
        request, db, account, active_nav="profile", saved=saved, error=error))


@router.post("/profile")
def hr_profile_save(account: HrAccount, db: Session = Depends(get_db), first_name: str = Form(""),
                    last_name: str = Form(""), phone: str = Form("")):
    hr = people.current_hr(db, account)
    if not hr:
        return redirect("/hr/profile", error="HR profile not found.")
    err = people.update_name(db, hr, first_name, last_name, phone)
    return redirect("/hr/profile", error=err, saved=None if err else "details")


@router.post("/profile/password")
def hr_password_save(request: Request, account: HrAccount, db: Session = Depends(get_db), current_password: str = Form(""),
                     new_password: str = Form(""), confirm_password: str = Form("")):
    err = change_password(db, account, current_password, new_password, confirm_password,
                          keep_refresh_token=request.cookies.get("refresh_token"),
                          ip=request.client.host if request.client else None)
    return redirect("/hr/profile", error=err, saved=None if err else "password")
