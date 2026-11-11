"""Leaves, holidays and attendance.

A day's status is worked out in this order: HR's correction, approved leave, the staff member's
check-in, a holiday, a week off; a past working day with none of these counts as absent.
Codes: P present, HALF half day worked, L leave, HL half-day leave, UL unpaid leave, A absent, H holiday, W week off,
"" future / not yet.

Leave balance for a year = days per year (+ unused days carried over from last year, when the
leave type allows it, up to its limit) − approved days. Pending requests are shown beside it.
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.core.templates import plain_number
from app.modules.hr.models.records import AttendanceDay, Holiday, LeaveRequest, LeaveType
from app.modules.hr.services import hr_settings
from app.modules.staff.models.staff import Staff
from app.modules.staff.models.staff_records import StaffEmploymentDetails
from app.platform.models.system import AuditLog

LOCAL_TZ = ZoneInfo("Asia/Kolkata")  # the office's clock: same zone as the scheduled jobs
OPEN = ("pending", "approved")
CORRECTIONS = [("P", "Present"), ("HALF", "Half day"), ("A", "Absent"), ("L", "On leave")]
CODE_LABELS = {"P": "Present", "HALF": "Half day", "L": "Leave", "HL": "Half-day leave", "UL": "Unpaid leave", "A": "Absent", "H": "Holiday",
               "W": "Week off", "": "Not yet"}


def today() -> date:
    return datetime.now(LOCAL_TZ).date()


def local_time(moment: datetime) -> str:
    """A stored time as the office clock shows it, e.g. 09:42 AM (SQLite hands back naive UTC)."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(LOCAL_TZ).strftime("%I:%M %p")


def _audit(db: Session, actor_id: str | None, action: str, entity_type: str, entity_id: str, old, new) -> None:
    db.add(AuditLog(actor_account_id=actor_id, action=action, entity_type=entity_type, entity_id=entity_id,
                    old_values=old, new_values=new))


# ── Holidays and working days ───────────────────────────────────────────────

def holidays(db: Session, year: int | None = None) -> list[Holiday]:
    q = db.query(Holiday)
    if year:
        q = q.filter(Holiday.day >= date(year, 1, 1), Holiday.day <= date(year, 12, 31))
    return q.order_by(Holiday.day).all()


def add_holiday(db: Session, actor_id: str, day: str, name: str) -> str | None:
    try:
        d = date.fromisoformat((day or "").strip())
    except ValueError:
        return "Enter the holiday's date."
    name = " ".join((name or "").split())[:80]
    if not name:
        return "Give the holiday a name."
    if db.query(Holiday.id).filter(Holiday.day == d).first():
        return f"{d.strftime('%d %b %Y')} is already a holiday."
    h = Holiday(day=d, name=name)
    db.add(h)
    db.flush()
    _audit(db, actor_id, "holiday_added", "holiday", h.id, None, {"day": str(d), "name": name})
    db.commit()
    return None


def remove_holiday(db: Session, actor_id: str, holiday_id: str) -> str | None:
    h = db.get(Holiday, holiday_id)
    if not h:
        return "Holiday not found."
    _audit(db, actor_id, "holiday_removed", "holiday", h.id, {"day": str(h.day), "name": h.name}, None)
    db.delete(h)
    db.commit()
    return None


def _holiday_days(db: Session, start: date, end: date) -> set[date]:
    return {d for (d,) in db.query(Holiday.day).filter(Holiday.day >= start, Holiday.day <= end)}


def working_days(db: Session, start: date, end: date, half_day: bool = False) -> Decimal:
    offs, hols = hr_settings.week_off_days(db), _holiday_days(db, start, end)
    n = sum(1 for i in range((end - start).days + 1)
            if (d := start + timedelta(days=i)).weekday() not in offs and d not in hols)
    return Decimal("0.5") if half_day and n else Decimal(n)


# ── Leave balances ──────────────────────────────────────────────────────────

def _used(db: Session, staff_id: str, type_id: int, year: int, status: str = "approved") -> Decimal:
    """Days of this type in this year (a request crossing New Year counts in the year it starts)."""
    rows = db.query(LeaveRequest.days).filter(
        LeaveRequest.staff_id == staff_id, LeaveRequest.leave_type_id == type_id, LeaveRequest.status == status,
        LeaveRequest.start_date >= date(year, 1, 1), LeaveRequest.start_date <= date(year, 12, 31))
    return sum((d for (d,) in rows), Decimal(0))


def _started_before(db: Session, staff: Staff, day: date) -> bool:
    joined = db.query(StaffEmploymentDetails.date_of_joining).filter(StaffEmploymentDetails.staff_id == staff.id).scalar()
    start = joined or (staff.created_at.date() if staff.created_at else day)
    return start < day


@dataclass
class Balance:
    leave_type: LeaveType
    allowance: Decimal | None  # None: no limit (e.g. unpaid leave)
    carried: Decimal
    used: Decimal
    pending: Decimal
    left: Decimal | None = field(default=None)


def balances(db: Session, staff: Staff, year: int | None = None) -> list[Balance]:
    year = year or today().year
    out = []
    for lt in hr_settings.leave_types(db, active_only=True):
        used, pending = _used(db, staff.id, lt.id, year), _used(db, staff.id, lt.id, year, "pending")
        if not lt.days_per_year:
            out.append(Balance(lt, None, Decimal(0), used, pending))
            continue
        carried = Decimal(0)
        if lt.carry_over and _started_before(db, staff, date(year, 1, 1)):
            carried = max(Decimal(lt.days_per_year) - _used(db, staff.id, lt.id, year - 1), Decimal(0))
            if lt.max_carry_over is not None:
                carried = min(carried, Decimal(lt.max_carry_over))
        allowance = Decimal(lt.days_per_year) + carried
        out.append(Balance(lt, allowance, carried, used, pending, allowance - used))
    return out


def _available(db: Session, staff: Staff, lt: LeaveType, year: int, own_pending: Decimal = Decimal(0)) -> Decimal | None:
    """Days that can still be asked for: left after approved leave, minus other requests still waiting."""
    b = next((b for b in balances(db, staff, year) if b.leave_type.id == lt.id), None)
    if not b or b.left is None:
        return None
    return b.left - (b.pending - own_pending)


# ── Leave requests ──────────────────────────────────────────────────────────

def requests(db: Session, staff: Staff | None = None, status: str | None = None, limit: int = 200) -> list[LeaveRequest]:
    q = db.query(LeaveRequest)
    if staff:
        q = q.filter(LeaveRequest.staff_id == staff.id)
    if status:
        q = q.filter(LeaveRequest.status == status)
    return q.order_by(LeaveRequest.start_date.desc(), LeaveRequest.created_at.desc()).limit(limit).all()


def _overlap(db: Session, staff: Staff, start: date, end: date, ignore_id: str | None = None) -> LeaveRequest | None:
    q = db.query(LeaveRequest).filter(LeaveRequest.staff_id == staff.id, LeaveRequest.status.in_(OPEN),
                                      LeaveRequest.start_date <= end, LeaveRequest.end_date >= start)
    if ignore_id:
        q = q.filter(LeaveRequest.id != ignore_id)
    return q.first()


def _parse(start: str, end: str) -> tuple[date, date]:
    try:
        s = date.fromisoformat((start or "").strip())
        e = date.fromisoformat((end or "").strip()) if (end or "").strip() else s
    except ValueError:
        raise ValueError("Enter the leave dates.")
    if e < s:
        raise ValueError("The last day can't be before the first day.")
    if (e - s).days > 120:
        raise ValueError("One request can cover at most 120 days.")
    return s, e


def _fits(db: Session, staff: Staff, lt: LeaveType, days: Decimal, year: int, own_pending: Decimal = Decimal(0)) -> str | None:
    left = _available(db, staff, lt, year, own_pending)
    if left is not None and days > left:
        left = max(left, Decimal(0))
        return (f"Only {plain_number(left)} day{'s' if left != 1 else ''} of {lt.name.lower()} can be asked for this year "
                "(requests still waiting count too).")
    return None


def apply(db: Session, actor_id: str, staff: Staff, type_id: str, start: str, end: str, half_day: bool, reason: str,
          by_hr: bool = False) -> tuple[LeaveRequest | None, str | None]:
    """A staff member's request (pending), or leave HR records for them (approved straight away)."""
    lt = db.get(LeaveType, int(type_id)) if str(type_id or "").isdigit() else None
    if not lt or not lt.is_active:
        return None, "Choose a leave type."
    try:
        s, e = _parse(start, end)
    except ValueError as err:
        return None, str(err)
    if half_day and s != e:
        return None, "A half day must start and end on the same day."
    if not by_hr and s < today() - timedelta(days=30):
        return None, "Leave more than 30 days back has to be added by HR."
    days = working_days(db, s, e, half_day)
    if not days:
        return None, "Those dates are all week offs or holidays."
    if _overlap(db, staff, s, e):
        return None, "You already have leave on some of these dates." if not by_hr else "This person already has leave on some of these dates."
    err = _fits(db, staff, lt, days, s.year)
    if err:
        return None, err
    req = LeaveRequest(staff_id=staff.id, leave_type_id=lt.id, start_date=s, end_date=e, half_day=bool(half_day), days=days,
                       reason=" ".join((reason or "").split())[:255] or None, created_by_account_id=actor_id,
                       status="approved" if by_hr else "pending")
    if by_hr:
        req.decided_by_account_id, req.decided_at = actor_id, datetime.now(timezone.utc)
    db.add(req)
    db.flush()
    _audit(db, actor_id, "leave_added_by_hr" if by_hr else "leave_requested", "leave_request", req.id, None,
           {"type": lt.code, "start": str(s), "end": str(e), "days": str(days), "status": req.status})
    db.commit()
    return req, None


def decide(db: Session, actor_id: str, req: LeaveRequest, approve: bool, note: str) -> str | None:
    if req.status != "pending":
        return "This request has already been decided."
    note = " ".join((note or "").split())[:255] or None
    if not approve and not note:
        return "Say why the request is rejected; the staff member sees it."
    if approve:
        err = _fits(db, req.staff, req.leave_type, req.days, req.start_date.year, own_pending=req.days)
        if err:
            return err
    req.status = "approved" if approve else "rejected"
    req.hr_note, req.decided_by_account_id, req.decided_at = note, actor_id, datetime.now(timezone.utc)
    _audit(db, actor_id, "leave_approved" if approve else "leave_rejected", "leave_request", req.id,
           {"status": "pending"}, {"status": req.status, "note": note})
    db.commit()
    return None


def cancel(db: Session, actor_id: str, req: LeaveRequest) -> str | None:
    """Staff cancel their own pending leave, or approved leave that hasn't started yet."""
    if req.status not in OPEN:
        return "This request can't be cancelled."
    if req.status == "approved" and req.start_date <= today():
        return "Leave that has already started can only be changed by HR."
    old = req.status
    req.status = "cancelled"
    _audit(db, actor_id, "leave_cancelled", "leave_request", req.id, {"status": old}, {"status": "cancelled"})
    db.commit()
    return None


# ── Attendance ──────────────────────────────────────────────────────────────

def _row(db: Session, staff: Staff, day: date) -> AttendanceDay | None:
    return db.query(AttendanceDay).filter(AttendanceDay.staff_id == staff.id, AttendanceDay.day == day).first()


def today_row(db: Session, staff: Staff) -> AttendanceDay | None:
    return _row(db, staff, today())


def check_in(db: Session, staff: Staff) -> str | None:
    day = today()
    if day.weekday() in hr_settings.week_off_days(db) or _holiday_days(db, day, day):
        return "Today is a week off or a holiday: no check-in needed."
    row = _row(db, staff, day)
    if row and row.check_in_at:
        return "You've already checked in today."
    now = datetime.now(timezone.utc)
    if row:
        row.check_in_at = now
    else:
        db.add(AttendanceDay(staff_id=staff.id, day=day, check_in_at=now))
    db.commit()
    return None


def check_out(db: Session, staff: Staff) -> str | None:
    row = _row(db, staff, today())
    if not row or not row.check_in_at:
        return "Check in first."
    if row.check_out_at:
        return "You've already checked out today."
    row.check_out_at = datetime.now(timezone.utc)
    db.commit()
    return None


def correct(db: Session, actor_id: str, staff: Staff, day: str, status: str, note: str) -> tuple[str | None, str | None]:
    """HR sets a day's status. Returns (old status label, error)."""
    try:
        d = date.fromisoformat((day or "").strip())
    except ValueError:
        return None, "Choose a day."
    if d > today():
        return None, "Attendance can't be set for a future day."
    if status not in dict(CORRECTIONS) and status != "CLEAR":
        return None, "Choose present, half day, absent or on leave."
    note = " ".join((note or "").split())[:255] or None
    old = month_codes(db, staff, d.year, d.month)[d.day - 1]
    row = _row(db, staff, d)
    if not row:
        row = AttendanceDay(staff_id=staff.id, day=d)
        db.add(row)
    row.status = None if status == "CLEAR" else status
    row.note, row.updated_by_account_id = note, actor_id
    db.flush()
    _audit(db, actor_id, "attendance_corrected", "staff", staff.id, {"day": str(d), "status": old},
           {"day": str(d), "status": status, "note": note})
    db.commit()
    return CODE_LABELS.get(old, old), None


def _leave_by_day(db: Session, staff_ids: list[str], start: date, end: date) -> dict[tuple[str, date], tuple[str, bool]]:
    """{(staff_id, day): (L or UL, half_day)} for approved leave in the range."""
    out = {}
    rows = (db.query(LeaveRequest, LeaveType.is_paid).join(LeaveType, LeaveType.id == LeaveRequest.leave_type_id)
            .filter(LeaveRequest.staff_id.in_(staff_ids or [""]), LeaveRequest.status == "approved",
                    LeaveRequest.start_date <= end, LeaveRequest.end_date >= start))
    for req, paid in rows:
        d = max(req.start_date, start)
        while d <= min(req.end_date, end):
            out[(req.staff_id, d)] = ("L" if paid else "UL", req.half_day)
            d += timedelta(days=1)
    return out


def month_grid(db: Session, staff_list: list[Staff], year: int, month: int) -> dict[str, list[str]]:
    """{staff_id: [code for day 1..n]} for the month."""
    n = calendar.monthrange(year, month)[1]
    start, end = date(year, month, 1), date(year, month, n)
    ids = [s.id for s in staff_list]
    offs, hols, now = hr_settings.week_off_days(db), _holiday_days(db, start, end), today()
    rows = {(r.staff_id, r.day): r for r in db.query(AttendanceDay).filter(
        AttendanceDay.staff_id.in_(ids or [""]), AttendanceDay.day >= start, AttendanceDay.day <= end)}
    leave = _leave_by_day(db, ids, start, end)
    grid = {}
    for sid in ids:
        codes = []
        for i in range(n):
            d = start + timedelta(days=i)
            row, lv = rows.get((sid, d)), leave.get((sid, d))
            if row and row.status:
                code = row.status
            elif lv:
                code = "HL" if lv[1] and lv[0] == "L" else lv[0]
            elif row and row.check_in_at:
                code = "P"
            elif d in hols:
                code = "H"
            elif d.weekday() in offs:
                code = "W"
            elif d < now:
                code = "A"
            else:
                code = ""
            codes.append(code)
        grid[sid] = codes
    return grid


def month_codes(db: Session, staff: Staff, year: int, month: int) -> list[str]:
    return month_grid(db, [staff], year, month)[staff.id]


def totals(codes: list[str]) -> dict[str, int]:
    return {c: codes.count(c) for c in ("P", "HALF", "L", "HL", "UL", "A", "H", "W")}


def not_checked_in_today(db: Session, staff_list: list[Staff]) -> list[Staff]:
    day = today()
    if day.weekday() in hr_settings.week_off_days(db) or _holiday_days(db, day, day):
        return []
    codes = month_grid(db, staff_list, day.year, day.month)
    return [s for s in staff_list if codes[s.id][day.day - 1] in ("", "A")]


def on_leave_today(db: Session, staff_list: list[Staff]) -> int:
    day = today()
    codes = month_grid(db, staff_list, day.year, day.month)
    return sum(1 for s in staff_list if codes[s.id][day.day - 1] in ("L", "UL", "HL"))


def parse_month(value: str | None) -> tuple[int, int]:
    """'2026-11' → (2026, 11); anything else → this month."""
    try:
        y, m = (int(p) for p in (value or "").split("-"))
        if 2000 <= y <= 2100 and 1 <= m <= 12:
            return y, m
    except ValueError:
        pass
    t = today()
    return t.year, t.month


def month_nav(year: int, month: int) -> dict:
    prev_m = (year, month - 1) if month > 1 else (year - 1, 12)
    next_m = (year, month + 1) if month < 12 else (year + 1, 1)
    return {"value": f"{year:04d}-{month:02d}", "label": date(year, month, 1).strftime("%B %Y"),
            "prev": f"{prev_m[0]:04d}-{prev_m[1]:02d}", "next": f"{next_m[0]:04d}-{next_m[1]:02d}",
            "days": [date(year, month, d) for d in range(1, calendar.monthrange(year, month)[1] + 1)]}

