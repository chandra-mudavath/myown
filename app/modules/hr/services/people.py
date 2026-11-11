"""What the HR workspace shows and changes: the staff list, one employee's record, job details and
salary changes. HR works on staff accounts only; clients, cases and admins never appear here.

Salary history is add-only: a change is a new staff_salary_history row and current_salary on the
employment record is updated in the same commit. Every change is written to audit_logs.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.modules.hr.models.hr import HR
from app.modules.staff.models.staff import Staff
from app.modules.staff.models.staff_records import StaffDocument, StaffEmploymentDetails, StaffSalaryHistory
from app.platform.models.auth import AccountType, AuthAccount
from app.platform.models.system import AuditLog
from app.platform.schemas.auth import _NAME_RE, _NAME_STRICT_RE

EMPLOYMENT_TYPES = [("FULL_TIME", "Full time"), ("PART_TIME", "Part time"), ("CONTRACT", "Contract"), ("INTERN", "Intern")]
EMPLOYMENT_STATUSES = [("ACTIVE", "Active"), ("PROBATION", "Probation"), ("ON_LEAVE", "On leave"),
                       ("NOTICE", "Notice period"), ("TERMINATED", "Left")]
_TYPE_LABEL, _STATUS_LABEL = dict(EMPLOYMENT_TYPES), dict(EMPLOYMENT_STATUSES)
EXPIRY_WINDOW_DAYS = 30


def current_hr(db: Session, account: AuthAccount) -> HR | None:
    return db.query(HR).filter(HR.account_id == account.id).first()


def staff_query(db: Session):
    """Staff logins only (a staff row left behind when its person moved to HR is not an employee here)."""
    return db.query(Staff).join(AuthAccount, AuthAccount.id == Staff.account_id).filter(AuthAccount.account_type == AccountType.STAFF)


def full_name(person) -> str:
    return " ".join(p for p in (person.first_name, person.last_name) if p)


def initials(person) -> str:
    return "".join(p[0] for p in (person.first_name, person.last_name) if p).upper() or "?"


def type_label(code: str | None) -> str:
    return _TYPE_LABEL.get(code or "", code or "—")


def status_label(code: str | None) -> str:
    return _STATUS_LABEL.get(code or "", code or "—")


# ── Dashboard ─────────────────────────────────────────────────────────────

def dashboard_stats(db: Session) -> dict:
    today = datetime.now(timezone.utc)
    month_start = today.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    staff = staff_query(db)
    total = staff.count()
    active = staff.filter(AuthAccount.is_active.is_(True)).count()
    with_record = (db.query(func.count(StaffEmploymentDetails.id))
                   .join(Staff, Staff.id == StaffEmploymentDetails.staff_id)
                   .join(AuthAccount, AuthAccount.id == Staff.account_id)
                   .filter(AuthAccount.account_type == AccountType.STAFF, AuthAccount.is_active.is_(True)).scalar() or 0)
    expiring = (db.query(func.count(StaffDocument.id))
                .filter(StaffDocument.expiry_date.isnot(None),
                        StaffDocument.expiry_date <= date.today() + timedelta(days=EXPIRY_WINDOW_DAYS)).scalar() or 0)
    return {
        "total": total,
        "active": active,
        "inactive": total - active,
        "joined_this_month": staff.filter(Staff.created_at >= month_start).count(),
        "missing_records": max(active - with_record, 0),
        "expiring_documents": expiring,
    }


def employee_rows(db: Session, q: str = "", status: str = "") -> list[dict]:
    query = staff_query(db).outerjoin(StaffEmploymentDetails, StaffEmploymentDetails.staff_id == Staff.id)
    term = (q or "").strip()
    if term:
        like = f"%{term}%"
        query = query.filter(or_(Staff.first_name.ilike(like), Staff.last_name.ilike(like), Staff.staff_number.ilike(like),
                                 AuthAccount.email.ilike(like), StaffEmploymentDetails.employee_number.ilike(like),
                                 StaffEmploymentDetails.department.ilike(like), StaffEmploymentDetails.designation.ilike(like)))
    if status == "active":
        query = query.filter(AuthAccount.is_active.is_(True))
    elif status == "inactive":
        query = query.filter(AuthAccount.is_active.is_(False))
    elif status == "missing":
        query = query.filter(StaffEmploymentDetails.id.is_(None), AuthAccount.is_active.is_(True))
    rows = []
    for s in query.order_by(Staff.first_name, Staff.last_name).add_columns(StaffEmploymentDetails).all():
        staff, record = s
        rows.append({
            "id": staff.id, "name": full_name(staff), "initials": initials(staff), "staff_number": staff.staff_number,
            "employee_number": record.employee_number if record else None, "email": staff.account.email,
            "designation": (record.designation if record else None) or staff.job_title,
            "department": record.department if record else None,
            "employment_status": status_label(record.employment_status) if record else None,
            "is_active": staff.account.is_active,
        })
    return rows


# ── One employee ──────────────────────────────────────────────────────────

def employee(db: Session, staff_id: str) -> Staff | None:
    return staff_query(db).filter(Staff.id == staff_id).first()


def employee_picker(db: Session) -> list[tuple[str, str]]:
    return [(s.id, f"{full_name(s)} · {s.staff_number}") for s in staff_query(db).order_by(Staff.first_name, Staff.last_name)]


def employment_record(db: Session, staff: Staff) -> StaffEmploymentDetails | None:
    return db.query(StaffEmploymentDetails).filter(StaffEmploymentDetails.staff_id == staff.id).first()


def salary_history(db: Session, staff: Staff) -> list[StaffSalaryHistory]:
    return (db.query(StaffSalaryHistory).filter(StaffSalaryHistory.staff_id == staff.id)
            .order_by(StaffSalaryHistory.effective_date.desc(), StaffSalaryHistory.created_at.desc()).all())


def documents(db: Session, staff: Staff) -> list[StaffDocument]:
    return db.query(StaffDocument).filter(StaffDocument.staff_id == staff.id).order_by(StaffDocument.uploaded_at.desc()).all()


def managers(db: Session, staff: Staff) -> list[tuple[str, str]]:
    """Who can be the reporting manager: any other active staff member."""
    return [(s.id, full_name(s)) for s in staff_query(db).filter(AuthAccount.is_active.is_(True), Staff.id != staff.id)
            .order_by(Staff.first_name, Staff.last_name)]


def _audit(db: Session, actor_id: str, action: str, entity_type: str, entity_id: str, old: dict | None, new: dict | None) -> None:
    db.add(AuditLog(actor_account_id=actor_id, action=action, entity_type=entity_type, entity_id=entity_id,
                    old_values=old, new_values=new))


def _date(value: str, label: str) -> date | None:
    value = (value or "").strip()
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValueError(f"Enter {label} as a date.")


def _text(value: str, limit: int) -> str | None:
    value = (value or "").strip()
    return value[:limit] or None


def save_job(db: Session, actor_id: str, staff: Staff, form: dict) -> str | None:
    """Create or update the employment record. Returns an error message, or None when saved."""
    try:
        joined = _date(form.get("date_of_joining", ""), "the date of joining")
        left = _date(form.get("termination_date", ""), "the last working day")
    except ValueError as e:
        return str(e)
    emp_type = form.get("employment_type") or None
    status = form.get("employment_status") or "ACTIVE"
    if emp_type and emp_type not in _TYPE_LABEL:
        return "Choose an employment type."
    if status not in _STATUS_LABEL:
        return "Choose a status."
    if status == "TERMINATED" and not left:
        return "Enter the last working day for someone who has left."
    if joined and left and left < joined:
        return "The last working day can't be before the date of joining."
    manager_id = form.get("reporting_manager_staff_id") or None
    if manager_id and (manager_id == staff.id or not employee(db, manager_id)):
        return "Choose a reporting manager from the staff list."

    record = employment_record(db, staff)
    old = None
    if record:
        old = {k: str(getattr(record, k)) if getattr(record, k) is not None else None
               for k in ("date_of_joining", "employment_type", "department", "designation", "reporting_manager_staff_id",
                         "employment_status", "termination_date")}
    else:
        record = StaffEmploymentDetails(staff_id=staff.id)
        db.add(record)
    record.date_of_joining, record.termination_date = joined, left
    record.employment_type, record.employment_status = emp_type, status
    record.department = _text(form.get("department", ""), 80)
    record.designation = _text(form.get("designation", ""), 80)
    record.reporting_manager_staff_id = manager_id
    record.updated_by_account_id = actor_id
    db.flush()
    new = {"date_of_joining": str(joined) if joined else None, "employment_type": emp_type, "department": record.department,
           "designation": record.designation, "reporting_manager_staff_id": manager_id, "employment_status": status,
           "termination_date": str(left) if left else None}
    _audit(db, actor_id, "employment_record_saved", "staff", staff.id, old, new)
    db.commit()
    return None


def add_salary_change(db: Session, actor_id: str, staff: Staff, new_salary: str, effective_date: str, reason: str) -> str | None:
    """Append a salary history row and update the current salary. Rows are never edited."""
    try:
        amount = Decimal((new_salary or "").replace(",", "").strip())
    except InvalidOperation:
        return "Enter the new monthly salary as a number."
    if amount <= 0 or amount >= Decimal("10000000000"):
        return "Enter a monthly salary above zero."
    try:
        effective = _date(effective_date, "the effective date")
    except ValueError as e:
        return str(e)
    if not effective:
        return "Enter the date the new salary starts."
    record = employment_record(db, staff)
    if not record:
        return "Save the job details first, then add the salary."
    previous = record.current_salary
    hike = ((amount - previous) / previous * 100).quantize(Decimal("0.01")) if previous else None
    if hike is not None and abs(hike) >= 1000:
        hike = None  # too large to store as a percentage; the two amounts still show the change
    db.add(StaffSalaryHistory(staff_id=staff.id, effective_date=effective, previous_salary=previous, new_salary=amount,
                              hike_percentage=hike, reason=_text(reason, 255), approved_by_account_id=actor_id))
    record.current_salary, record.updated_by_account_id = amount, actor_id
    _audit(db, actor_id, "salary_changed", "staff", staff.id,
           {"current_salary": str(previous) if previous is not None else None}, {"current_salary": str(amount), "effective_date": str(effective)})
    db.commit()
    return None


# ── Payroll details (PAN, UAN, PF number, bank) ──────────────────────────

PAYROLL_FIELDS = [("pan", "PAN"), ("uan", "UAN"), ("pf_number", "PF number"), ("bank_name", "Bank name"),
                  ("bank_account", "Bank account number"), ("ifsc", "IFSC")]
_PAYROLL_RULES = {
    "pan": (re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$"), "PAN should look like ABCDE1234F."),
    "uan": (re.compile(r"^[0-9]{12}$"), "UAN should be 12 digits."),
    "pf_number": (re.compile(r"^[A-Z0-9/ -]{5,30}$"), "PF number can have letters, digits and / (5 to 30 characters)."),
    "bank_name": (re.compile(r"^[A-Za-z0-9 .&()-]{2,60}$"), "Enter the bank's name."),
    "bank_account": (re.compile(r"^[0-9]{6,18}$"), "Bank account number should be 6 to 18 digits."),
    "ifsc": (re.compile(r"^[A-Z]{4}0[A-Z0-9]{6}$"), "IFSC should look like HDFC0001234."),
}


def save_payroll(db: Session, actor_id: str, staff: Staff, form: dict) -> str | None:
    """PAN, UAN, PF number and bank details. Stored encrypted; the audit log names the fields, never the values."""
    record = employment_record(db, staff)
    if not record:
        return "Save the job details first, then the payroll details."
    values = {}
    for key, _ in PAYROLL_FIELDS:
        raw = " ".join(str(form.get(key, "")).split())
        value = raw if key == "bank_name" else raw.upper().replace(" ", "") if key != "pf_number" else raw.upper()
        if value:
            rule, message = _PAYROLL_RULES[key]
            if not rule.match(value):
                return message
        values[key] = value or None
    old = dict(record.payroll_ids or {})
    changed = sorted(k for k in values if values[k] != old.get(k))
    if not changed:
        return None
    record.payroll_ids, record.updated_by_account_id = values, actor_id
    _audit(db, actor_id, "payroll_details_saved", "staff", staff.id, None, {"fields_changed": changed})
    db.commit()
    return None


def payroll_view(record: StaffEmploymentDetails | None, masked: bool = True) -> dict:
    """Payroll details for a page or payslip. PAN and account number are masked unless masked=False."""
    ids = dict((record.payroll_ids or {}) if record else {})
    pan, account = ids.get("pan") or "", ids.get("bank_account") or ""
    if masked:
        ids["pan"] = f"{pan[:5]}****{pan[-1:]}" if len(pan) == 10 else pan
        ids["bank_account"] = f"****{account[-4:]}" if account else ""
    return {key: ids.get(key) or "" for key, _ in PAYROLL_FIELDS}


# ── HR's own profile ──────────────────────────────────────────────────────

def update_name(db: Session, hr: HR, first_name: str, last_name: str | None, phone: str | None) -> str | None:
    """Same name rules as sign-up."""
    first = (first_name or "").strip()
    last = (last_name or "").strip() or None
    if not first:
        return "First name is required."
    if len(first) > 15 or (last and len(last) > 15):
        return "Names can be at most 15 characters."
    if not _NAME_RE.match(first):
        return "First name must contain only letters and numbers."
    if last and not _NAME_STRICT_RE.match(last):
        return "Last name must contain only letters."
    phone = (phone or "").strip() or None
    if phone and len(phone) > 20:
        return "Enter a phone number of at most 20 characters."
    hr.first_name, hr.last_name, hr.phone = first, last, phone
    db.commit()
    return None
