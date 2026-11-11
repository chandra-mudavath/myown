"""HR settings, kept in the app_settings table (key "hr.<name>") like the other app-wide settings.

Defaults stand in until HR saves their own values on the HR Settings page. The leave policy
values are placeholders: the real leave types, days per year and carry-over limits are still to
be agreed, and HR can change them at any time without a code change.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from sqlalchemy.orm import Session

from app.core.config import settings as app_config
from app.modules.hr.models.records import LeaveType
from app.platform.models.lookups import DocumentCategory, DocumentType
from app.platform.models.system import AppSetting, AuditLog

# (key, label, default, kind)
FIELDS = [
    # Letterhead and signature on every HR document
    ("company_name", "Company name", app_config.APP_NAME, "text"),
    ("company_address", "Registered address", "", "text"),
    ("company_phone", "Phone", "", "text"),
    ("company_website", "Website", "covrenttax.com", "text"),
    ("company_reg", "Registration details (footer)", "", "text"),
    ("signatory_name", "Authorised signatory", "", "text"),
    ("signatory_title", "Signatory's designation", "HR Manager", "text"),
    ("hr_email", "HR email for staff questions", "hr@covrenttax.com", "text"),
    ("hr_phone", "HR phone", "", "text"),
    ("company_pf_code", "Company PF establishment code (on payslips)", "", "text"),
    # Working time
    ("week_offs", "Week offs", "SAT,SUN", "weekdays"),
    ("check_in_cutoff", "Check in by (24h, e.g. 10:00)", "10:00", "time"),
    ("working_hours", "Working hours (shown in offer letters)", "Monday to Friday, 10:00 AM to 7:00 PM", "text"),
    # Offer letter pages (one paragraph or point per line)
    ("about_company", "About the company", "CovrentTax prepares and files US tax returns for individuals and businesses: "
     "individual and business returns, amendments and extensions, FBAR and FATCA reporting, ITIN applications and tax "
     "planning. Our team works with clients from the first document to the IRS acknowledgement.", "textarea"),
    ("work_culture", "Work culture", "We take ownership of every return we work on and check our work before it reaches a client.\n"
     "We keep client information private and share it only with the people working on the case.\n"
     "We ask for help early and explain our reasoning, so reviews are quick and learning is shared.\n"
     "We respect each other's time: meetings start on time and messages get a reply within the working day.", "textarea"),
    ("policies", "Policies", "Code of conduct: treat colleagues and clients with respect; harassment of any kind is not tolerated.\n"
     "Confidentiality: client and company information stays within the company, during and after your employment.\n"
     "Data security: use only company systems and accounts for client work; never copy client data to personal devices.\n"
     "Attendance: check in from the staff portal each working day; apply for leave in advance through the portal.\n"
     "Background verification: this offer depends on satisfactory verification of your documents and previous employment.\n"
     "Notice period: either side may end the employment with written notice as stated in this letter.", "textarea"),
    # Payslip breakup of the monthly salary
    ("basic_pct", "Basic, % of monthly salary", "50", "number"),
    ("hra_pct", "HRA, % of monthly salary", "25", "number"),
    ("pf_pct", "Employee PF, % of basic", "12", "number"),
    ("pf_cap", "PF limit per month (0 = no limit)", "1800", "number"),
    ("professional_tax", "Professional tax per month", "200", "number"),
    ("currency", "Currency shown on documents", "INR", "text"),
    # Which emails are sent (comma-separated email numbers from the plan)
    ("emails_on", "Emails turned on", "1,4,9,11,12,18", "text"),
]
DEFAULTS = {key: default for key, _, default, _ in FIELDS}
_KIND = {key: kind for key, _, _, kind in FIELDS}
WEEKDAYS = [("MON", "Mon"), ("TUE", "Tue"), ("WED", "Wed"), ("THU", "Thu"), ("FRI", "Fri"), ("SAT", "Sat"), ("SUN", "Sun")]
_WEEKDAY_INDEX = {code: i for i, (code, _) in enumerate(WEEKDAYS)}

DEFAULT_LEAVE_TYPES = [  # placeholders until the leave policy is agreed
    dict(code="CASUAL", name="Casual leave", days_per_year=12, is_paid=True, carry_over=False, max_carry_over=None, sort_order=1),
    dict(code="SICK", name="Sick leave", days_per_year=8, is_paid=True, carry_over=False, max_carry_over=None, sort_order=2),
    dict(code="EARNED", name="Earned leave", days_per_year=15, is_paid=True, carry_over=True, max_carry_over=30, sort_order=3),
    dict(code="UNPAID", name="Unpaid leave", days_per_year=0, is_paid=False, carry_over=False, max_carry_over=None, sort_order=4),
]
HR_DOCUMENT_CATEGORY = ("HR", "HR documents")
HR_DOCUMENT_TYPES = [("HR_OFFER_LETTER", "Offer letter"), ("HR_ID_PROOF", "ID proof"), ("HR_ADDRESS_PROOF", "Address proof"),
                     ("HR_CONTRACT", "Employment contract"), ("HR_EDUCATION", "Education certificate"),
                     ("HR_PREVIOUS_RELIEVING", "Previous employer's relieving letter"), ("HR_BANK", "Bank details"),
                     ("HR_OTHER", "Other HR document")]


def get_all(db: Session) -> dict[str, str]:
    rows = {r.key[3:]: r.value for r in db.query(AppSetting).filter(AppSetting.key.like("hr.%"))}
    return {key: rows.get(key) if rows.get(key) is not None else default for key, default in DEFAULTS.items()}


def get(db: Session, key: str) -> str:
    row = db.get(AppSetting, f"hr.{key}")
    return row.value if row and row.value is not None else DEFAULTS[key]


def number(db: Session, key: str) -> Decimal:
    try:
        return Decimal(get(db, key))
    except InvalidOperation:
        return Decimal(DEFAULTS[key])


def week_off_days(db: Session) -> set[int]:
    """Python weekday numbers (Mon = 0) that are week offs."""
    return {_WEEKDAY_INDEX[c] for c in get(db, "week_offs").split(",") if c in _WEEKDAY_INDEX}


def emails_on(db: Session) -> set[int]:
    return {int(n) for n in get(db, "emails_on").replace(" ", "").split(",") if n.isdigit()}


def save(db: Session, actor_id: str, form: dict) -> str | None:
    current = get_all(db)
    new: dict[str, str] = {}
    for key, label, _, kind in FIELDS:
        if kind == "weekdays":
            value = ",".join(code for code, _ in WEEKDAYS if form.get(f"week_off_{code}"))
        elif kind == "textarea":
            value = "\n".join(" ".join(line.split()) for line in str(form.get(key, "")).splitlines() if line.strip())[:4000]
        else:
            value = " ".join(str(form.get(key, "")).split())[:500]
        if kind == "number":
            try:
                if Decimal(value or "0") < 0:
                    raise InvalidOperation
            except InvalidOperation:
                return f"{label}: enter a number."
            value = value or "0"
        if kind == "time" and value:
            parts = value.split(":")
            if len(parts) != 2 or not all(p.isdigit() for p in parts) or int(parts[0]) > 23 or int(parts[1]) > 59:
                return f"{label}: enter a time such as 10:00."
        new[key] = value
    if Decimal(new["basic_pct"]) + Decimal(new["hra_pct"]) > 100:
        return "Basic and HRA together can't be more than 100% of the salary."
    now = datetime.now(timezone.utc)
    changed = {k: v for k, v in new.items() if v != current.get(k)}
    for key, value in changed.items():
        row = db.get(AppSetting, f"hr.{key}")
        if row:
            row.value, row.updated_by_account_id, row.updated_at = value, actor_id, now
        else:
            db.add(AppSetting(key=f"hr.{key}", value=value, updated_by_account_id=actor_id, updated_at=now))
    if changed:
        db.add(AuditLog(actor_account_id=actor_id, action="hr_settings_saved", entity_type="app_setting", entity_id="hr",
                        old_values={k: current.get(k) for k in changed}, new_values=changed))
    db.commit()
    return None


def ensure_defaults(db: Session) -> None:
    """Default leave types and HR document types, added once (never overwrites HR's changes)."""
    added = False
    if not db.query(LeaveType.id).first():
        for row in DEFAULT_LEAVE_TYPES:
            db.add(LeaveType(**row, is_active=True))
        added = True
    code, name = HR_DOCUMENT_CATEGORY
    if not db.query(DocumentCategory.id).filter(DocumentCategory.code == code).first():
        db.add(DocumentCategory(code=code, name=name, sort_order=90, is_active=True))
        db.flush()
        added = True
    have = {c for (c,) in db.query(DocumentType.code).filter(DocumentType.applies_to == "STAFF")}
    for i, (type_code, type_name) in enumerate(HR_DOCUMENT_TYPES, 1):
        if type_code not in have:
            db.add(DocumentType(code=type_code, name=type_name, category=code, applies_to="STAFF", sort_order=i, is_active=True))
            added = True
    if added:
        db.commit()


# ── Leave types (HR settings page) ─────────────────────────────────────────

def leave_types(db: Session, active_only: bool = False) -> list[LeaveType]:
    q = db.query(LeaveType)
    if active_only:
        q = q.filter(LeaveType.is_active.is_(True))
    return q.order_by(LeaveType.sort_order, LeaveType.id).all()


def save_leave_type(db: Session, actor_id: str, type_id: int | None, form: dict) -> str | None:
    name = " ".join(str(form.get("name", "")).split())
    if not name or len(name) > 80:
        return "Give the leave type a name (up to 80 characters)."
    try:
        days = int(form.get("days_per_year") or 0)
        cap = int(form["max_carry_over"]) if str(form.get("max_carry_over", "")).strip() else None
    except ValueError:
        return "Days must be whole numbers."
    if not 0 <= days <= 366 or (cap is not None and not 0 <= cap <= 366):
        return "Days must be between 0 and 366."
    values = dict(name=name, days_per_year=days, is_paid=bool(form.get("is_paid")), carry_over=bool(form.get("carry_over")),
                  max_carry_over=cap, is_active=bool(form.get("is_active")) if type_id else True)
    if type_id:
        lt = db.get(LeaveType, type_id)
        if not lt:
            return "Leave type not found."
        old = {k: getattr(lt, k) for k in values}
        for k, v in values.items():
            setattr(lt, k, v)
        action, entity = "leave_type_updated", str(lt.id)
    else:
        existing = {c for (c,) in db.query(LeaveType.code)}
        base = "".join(ch if ch.isalnum() else "_" for ch in name.upper()).strip("_")[:24] or "LEAVE"
        code, n = base, 2
        while code in existing:
            code, n = f"{base}_{n}", n + 1
        order = (db.query(LeaveType.sort_order).order_by(LeaveType.sort_order.desc()).limit(1).scalar() or 0) + 1
        lt = LeaveType(code=code, sort_order=order, **values)
        db.add(lt)
        db.flush()
        old, action, entity = None, "leave_type_added", str(lt.id)
    db.add(AuditLog(actor_account_id=actor_id, action=action, entity_type="leave_type", entity_id=entity,
                    old_values=old, new_values=values))
    db.commit()
    return None
