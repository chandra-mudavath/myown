"""HR workspace: attendance (month grid and corrections), leave requests and HR settings
(letterhead, working time, payslip breakup, emails, leave types, holidays)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import HrAccount
from app.core.templates import templates
from app.modules.hr.api.common import employee_or_404, hr_ctx, redirect
from app.modules.hr.models.records import LeaveRequest
from app.modules.hr.services import hr_settings, notify, people, time_off
from app.platform.models.auth import AuthAccount

router = APIRouter(prefix="/hr", tags=["hr"])


# ── Attendance ────────────────────────────────────────────────────────────

@router.get("/attendance", response_class=HTMLResponse, name="hr_attendance")
def hr_attendance(request: Request, account: HrAccount, db: Session = Depends(get_db), month: str = "",
                  saved: str = "", error: str = ""):
    y, m = time_off.parse_month(month)
    staff = people.staff_query(db).filter(AuthAccount.is_active.is_(True)).order_by(people.Staff.first_name).all()
    grid = time_off.month_grid(db, staff, y, m)
    rows = [{"staff": s, "name": people.full_name(s), "codes": grid[s.id], "totals": time_off.totals(grid[s.id])} for s in staff]
    return templates.TemplateResponse(request, "hr/attendance.html", hr_ctx(
        request, db, account, active_nav="attendance", nav=time_off.month_nav(y, m), rows=rows,
        code_labels=time_off.CODE_LABELS, corrections=time_off.CORRECTIONS, today=time_off.today(), saved=saved, error=error))


@router.post("/attendance/correct")
def hr_correct_attendance(request: Request, account: HrAccount, db: Session = Depends(get_db), staff_id: str = Form(""),
                          day: str = Form(""), status: str = Form(""), note: str = Form(""), back: str = Form("")):
    staff = employee_or_404(db, staff_id)
    old, err = time_off.correct(db, account.id, staff, day, status, note)
    if not err:
        new = time_off.CODE_LABELS.get(status, "as worked out from check-in and leave") if status != "CLEAR" else "as worked out from check-in and leave"
        notify.attendance_corrected(db, notify.base_url(request), staff, day, old, new, note)
    target = back if back.startswith("/hr/") and not back.startswith("//") else f"/hr/attendance?month={day[:7]}"
    return redirect(target, error=err, saved=None if err else f"Attendance for {people.full_name(staff)} on {day} saved.")


# ── Leaves ────────────────────────────────────────────────────────────────

@router.get("/leaves", response_class=HTMLResponse, name="hr_leaves")
def hr_leaves(request: Request, account: HrAccount, db: Session = Depends(get_db), status: str = "pending",
              saved: str = "", error: str = ""):
    status = status if status in ("pending", "approved", "rejected", "cancelled", "all") else "pending"
    staff = people.staff_query(db).filter(AuthAccount.is_active.is_(True)).order_by(people.Staff.first_name).all()
    return templates.TemplateResponse(request, "hr/leaves.html", hr_ctx(
        request, db, account, active_nav="leaves", status=status,
        leave_requests=time_off.requests(db, status=None if status == "all" else status),
        staff_options=[(s.id, people.full_name(s)) for s in staff], leave_types=hr_settings.leave_types(db, active_only=True),
        people=people, saved=saved, error=error))


def _request_or_404(db: Session, request_id: str) -> LeaveRequest:
    req = db.get(LeaveRequest, request_id)
    if not req or not people.employee(db, req.staff_id):
        raise HTTPException(status_code=404, detail="Leave request not found")
    return req


@router.post("/leaves/{request_id}/decide")
def hr_decide_leave(request: Request, request_id: str, account: HrAccount, db: Session = Depends(get_db),
                    decision: str = Form(""), note: str = Form(""), back: str = Form("")):
    req = _request_or_404(db, request_id)
    err = time_off.decide(db, account.id, req, decision == "approve", note)
    if not err:
        notify.leave_decided(db, notify.base_url(request), req)
    target = back if back.startswith("/hr/") and not back.startswith("//") else "/hr/leaves"
    return redirect(target, error=err, saved=None if err else f"Leave {req.status}.")


@router.post("/leaves/add")
def hr_add_leave(request: Request, account: HrAccount, db: Session = Depends(get_db), staff_id: str = Form(""),
                 leave_type_id: str = Form(""), start_date: str = Form(""), end_date: str = Form(""),
                 half_day: str = Form(""), reason: str = Form(""), back: str = Form("")):
    staff = employee_or_404(db, staff_id)
    req, err = time_off.apply(db, account.id, staff, leave_type_id, start_date, end_date, bool(half_day), reason, by_hr=True)
    if req:
        notify.leave_added(db, notify.base_url(request), req)
    target = back if back.startswith("/hr/") and not back.startswith("//") else "/hr/leaves?status=approved"
    return redirect(target, error=err, saved=None if err else f"Leave recorded for {people.full_name(staff)}.")


# ── Settings ──────────────────────────────────────────────────────────────

@router.get("/settings", response_class=HTMLResponse, name="hr_settings")
def hr_settings_page(request: Request, account: HrAccount, db: Session = Depends(get_db), year: int | None = None,
                     saved: str = "", error: str = ""):
    year = year or time_off.today().year
    return templates.TemplateResponse(request, "hr/settings.html", hr_ctx(
        request, db, account, active_nav="settings", values=hr_settings.get_all(db), fields=hr_settings.FIELDS,
        weekdays=hr_settings.WEEKDAYS, emails=notify.EVENTS, emails_on=hr_settings.emails_on(db),
        leave_types=hr_settings.leave_types(db), holidays=time_off.holidays(db, year), year=year, saved=saved, error=error))


@router.post("/settings")
async def hr_settings_save(request: Request, account: HrAccount, db: Session = Depends(get_db)):
    form = dict(await request.form())
    form["emails_on"] = ",".join(str(n) for n in notify.EVENTS if form.get(f"email_{n}"))
    err = hr_settings.save(db, account.id, form)
    return redirect("/hr/settings", error=err, saved=None if err else "Settings saved.")


@router.post("/settings/leave-types")
async def hr_leave_type_save(request: Request, account: HrAccount, db: Session = Depends(get_db)):
    form = dict(await request.form())
    type_id = int(form["id"]) if str(form.get("id", "")).isdigit() else None
    err = hr_settings.save_leave_type(db, account.id, type_id, form)
    return redirect("/hr/settings#leave-types", error=err, saved=None if err else "Leave type saved.")


@router.post("/settings/holidays")
def hr_holiday_add(account: HrAccount, db: Session = Depends(get_db), day: str = Form(""), name: str = Form("")):
    err = time_off.add_holiday(db, account.id, day, name)
    return redirect(f"/hr/settings?year={day[:4] if day[:4].isdigit() else ''}#holidays", error=err,
                    saved=None if err else "Holiday added.")


@router.post("/settings/holidays/{holiday_id}/remove")
def hr_holiday_remove(holiday_id: str, account: HrAccount, db: Session = Depends(get_db)):
    err = time_off.remove_holiday(db, account.id, holiday_id)
    return redirect("/hr/settings#holidays", error=err, saved=None if err else "Holiday removed.")
