"""HR emails: the 24 drafts from the HR plan, each one sent only when HR has turned it on
(HR Settings → Emails; hr_settings "emails_on"). They go out through the existing email
service and Celery task, like sign-in codes, so a slow mail server never holds up a page.

Safety rules for every email: no temporary passwords, no salary figures (except the offer
letter going to the candidate), and payslips are never attached: staff sign in to see them.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.core.config import settings
from app.modules.admin.models.admin import Admin
from app.modules.hr.models.hr import HR
from app.modules.hr.services import hr_settings
from app.platform.models.auth import AccountType, AuthAccount
from app.platform.tasks import email_tasks

# number → (name shown in HR Settings, who gets it)
EVENTS = {
    1: ("New staff account", "Employee"), 2: ("Account deactivated", "Employee"), 3: ("Account reactivated", "Employee"),
    4: ("Offer letter", "Candidate"), 5: ("Offer reminder", "Candidate"), 6: ("Offer accepted", "HR and admins"),
    7: ("Offer withdrawn", "Candidate"), 8: ("Welcome and joining details", "Candidate"),
    9: ("New leave request", "HR"), 10: ("Leave request received", "Employee"), 11: ("Leave approved", "Employee"),
    12: ("Leave rejected", "Employee"), 13: ("Leave cancelled by staff", "HR"), 14: ("Leave added by HR", "Employee"),
    15: ("Missed check-in reminder", "Employee"), 16: ("Attendance corrected", "Employee"),
    17: ("Daily attendance summary", "HR"), 18: ("Payslip ready", "Employee"), 19: ("Payslips finalised", "Admins"),
    20: ("Salary revision", "Employee"), 21: ("Probation confirmed", "Employee"), 22: ("Documents expiring", "HR"),
    23: ("Renewed document needed", "Employee"), 24: ("Relieving and experience letters", "Former employee"),
}


def base_url(request=None) -> str:
    return (settings.PUBLIC_BASE_URL or (str(request.base_url) if request else "")).rstrip("/")


def _d(value) -> str:
    if isinstance(value, date):
        return value.strftime("%d %b %Y")
    try:
        return date.fromisoformat(str(value)).strftime("%d %b %Y")
    except ValueError:
        return str(value or "")


def _days(value) -> str:
    text = f"{value}".rstrip("0").rstrip(".") if "." in f"{value}" else f"{value}"
    return f"{text} day{'' if text == '1' else 's'}"


def send(db: Session, number: int, to: str | list[str], subject: str, lines: list[str], link: tuple[str, str] | None = None,
         letter_html: str | None = None, greeting: str | None = None) -> bool:
    """Queue one HR email (or the same one to several people). False when that email is turned off."""
    if number not in hr_settings.emails_on(db):
        return False
    company = hr_settings.get_all(db)
    for address in ([to] if isinstance(to, str) else to):
        if not address:
            continue
        email_tasks.enqueue(email_tasks.send_hr_notice, address, subject, greeting or "", lines,
                            list(link) if link else None, letter_html, company["company_name"], company["hr_email"])
    return True


def hr_emails(db: Session) -> list[str]:
    return [e for (e,) in db.query(AuthAccount.email).join(HR, HR.account_id == AuthAccount.id)
            .filter(AuthAccount.is_active.is_(True), AuthAccount.account_type == AccountType.HR)]


def admin_emails(db: Session) -> list[str]:
    return [e for (e,) in db.query(AuthAccount.email).join(Admin, Admin.account_id == AuthAccount.id)
            .filter(AuthAccount.is_active.is_(True))]


# ── Accounts ──────────────────────────────────────────────────────────────

def account_created(db: Session, base: str, first_name: str, email: str, number: str, created_by: str) -> None:
    send(db, 1, email, f"Your {settings.APP_NAME} account is ready", [
        f"Welcome to {settings.APP_NAME}. Your account has been created.",
        f"Sign-in email: {email}\nEmployee number: {number}",
        f"Your temporary password will be shared with you separately by {created_by}. You'll be asked to choose your own "
        "password the first time you sign in, and each sign-in also needs a 6-digit code sent to this address.",
        f"If you didn't expect this email, contact HR at {hr_settings.get(db, 'hr_email')}.",
    ], ("Sign in", f"{base}/auth/login"), greeting=f"Hi {first_name},")


def account_active(db: Session, base: str, first_name: str, email: str, active: bool) -> None:
    if active:
        send(db, 3, email, f"Your {settings.APP_NAME} staff account is active again", [
            f"Your {settings.APP_NAME} staff account has been reactivated. You can sign in again with your usual password.",
        ], ("Sign in", f"{base}/auth/login"), greeting=f"Hi {first_name},")
    else:
        send(db, 2, email, f"Your {settings.APP_NAME} staff account has been deactivated", [
            f"Your {settings.APP_NAME} staff account ({email}) was deactivated on {_d(date.today())}. "
            "You can no longer sign in to the staff portal.",
            f"If you think this is a mistake, contact HR at {hr_settings.get(db, 'hr_email')}.",
        ], greeting=f"Hi {first_name},")


# ── Hiring ────────────────────────────────────────────────────────────────

def offer_sent(db: Session, doc, letter_html: str) -> bool:
    d = doc.data
    return send(db, 4, d["candidate_email"], f"Offer of employment: {d['designation']} at {settings.APP_NAME}", [
        "Thank you for your time during the interview process. We are pleased to offer you the position of "
        f"{d['designation']} in our {d['department']} team, with a joining date of {_d(d['joining_date'])}.",
        f"Your offer letter ({doc.document_number}) is below. It sets out your role, compensation and terms of employment.",
        f"Please reply to this email to accept the offer by {_d(d['accept_by'])}. If you have any questions, write to us"
        + (f" or call {hr_settings.get(db, 'hr_phone')}." if hr_settings.get(db, 'hr_phone') else "."),
        "We look forward to welcoming you.",
    ], letter_html=letter_html, greeting=f"Dear {d['candidate_name']},")


def offer_reminder(db: Session, doc, letter_html: str) -> bool:
    d = doc.data
    return send(db, 5, d["candidate_email"], f"Reminder: your {settings.APP_NAME} offer is open until {_d(d['accept_by'])}", [
        f"This is a reminder that our offer for the {d['designation']} position is open until {_d(d['accept_by'])}. "
        "The offer letter is below again for convenience.",
        "Please let us know your decision by replying to this email.",
    ], letter_html=letter_html, greeting=f"Dear {d['candidate_name']},")


def offer_accepted(db: Session, base: str, doc) -> None:
    d = doc.data
    send(db, 6, hr_emails(db) + admin_emails(db), f"Offer accepted: {d['candidate_name']}, joining {_d(d['joining_date'])}", [
        f"{d['candidate_name']} has accepted the offer {doc.document_number} for {d['designation']} ({d['department']}).",
        f"Joining date: {_d(d['joining_date'])}" + (f"\nReporting manager: {d['reporting_manager']}" if d.get("reporting_manager") else ""),
        "Next steps: create the staff account from the offer, then generate the appointment letter on the joining day.",
    ], ("Open the offer", f"{base}/hr/documents/{doc.id}"))


def offer_withdrawn(db: Session, doc, reason: str = "") -> None:
    d = doc.data
    send(db, 7, d["candidate_email"], f"Update on your offer from {settings.APP_NAME}", [
        f"We are writing about our offer {doc.document_number} for the {d['designation']} position. "
        "We regret to let you know that the offer has been withdrawn.",
        *([reason] if reason else []),
        f"Thank you for your interest in {settings.APP_NAME}, and we wish you the very best.",
    ], greeting=f"Dear {d['candidate_name']},")


def welcome(db: Session, doc) -> bool:
    d = doc.data
    company = hr_settings.get_all(db)
    return send(db, 8, d["candidate_email"], f"Welcome to {settings.APP_NAME}: your first day on {_d(d['joining_date'])}", [
        "We're glad you're joining us. Here is what you need for your first day.",
        f"Date and time: {_d(d['joining_date'])}{', ' + d['reporting_time'] if d.get('reporting_time') else ''}\n"
        f"Place: {d.get('work_location') or company['company_address']}"
        + (f"\nAsk for: {d['reporting_manager']}" if d.get("reporting_manager") else ""),
        "Please bring originals and one copy of: ID proof, address proof, education certificates, your previous employer's "
        "relieving letter, two passport-size photos, and your bank account details.",
        "Your company email and staff portal account will be ready on your first day.",
    ], greeting=f"Dear {d['candidate_name'].split()[0]},")


# ── Leaves ────────────────────────────────────────────────────────────────

def _leave_facts(req) -> str:
    return (f"Type: {req.leave_type.name}\nDates: {_d(req.start_date)} to {_d(req.end_date)} ({_days(req.days)})"
            + ("\nHalf day" if req.half_day else ""))


def leave_requested(db: Session, base: str, req) -> None:
    staff = req.staff
    name = f"{staff.first_name} {staff.last_name or ''}".strip()
    send(db, 9, hr_emails(db), f"Leave request: {name}, {req.leave_type.name}, {_d(req.start_date)} to {_d(req.end_date)}", [
        f"{name} ({staff.staff_number}) has applied for leave.",
        _leave_facts(req) + (f"\nReason: {req.reason}" if req.reason else ""),
    ], ("Approve or reject", f"{base}/hr/leaves"))
    send(db, 10, staff.account.email, "We've received your leave request", [
        f"Your request for {req.leave_type.name.lower()} from {_d(req.start_date)} to {_d(req.end_date)} ({_days(req.days)}) "
        "has been sent to HR. You'll get an email once it's approved or rejected.",
        "You can see or cancel the request under My leaves in your staff portal.",
    ], ("My leaves", f"{base}/staff/hr/leaves"), greeting=f"Hi {staff.first_name},")


def leave_decided(db: Session, base: str, req) -> None:
    staff = req.staff
    if req.status == "approved":
        send(db, 11, staff.account.email, f"Your leave from {_d(req.start_date)} to {_d(req.end_date)} is approved", [
            f"Your {req.leave_type.name.lower()} from {_d(req.start_date)} to {_d(req.end_date)} ({_days(req.days)}) has been approved.",
            *([req.hr_note] if req.hr_note else []),
        ], ("My leaves", f"{base}/staff/hr/leaves"), greeting=f"Hi {staff.first_name},")
    else:
        send(db, 12, staff.account.email, f"Your leave request for {_d(req.start_date)} to {_d(req.end_date)} was not approved", [
            f"Your {req.leave_type.name.lower()} request for {_d(req.start_date)} to {_d(req.end_date)} was not approved.",
            f"Reason: {req.hr_note}",
            "Your leave balance has not changed. If you'd like to discuss other dates, reply to this email or message HR from your portal.",
        ], ("My leaves", f"{base}/staff/hr/leaves"), greeting=f"Hi {staff.first_name},")


def leave_cancelled(db: Session, base: str, req) -> None:
    staff = req.staff
    name = f"{staff.first_name} {staff.last_name or ''}".strip()
    send(db, 13, hr_emails(db), f"Leave cancelled: {name}, {_d(req.start_date)} to {_d(req.end_date)}", [
        f"{name} ({staff.staff_number}) has cancelled their {req.leave_type.name.lower()} for {_d(req.start_date)} to {_d(req.end_date)}.",
        f"The {_days(req.days)} have been returned to their balance.",
    ], ("Leaves", f"{base}/hr/leaves"))


def leave_added(db: Session, base: str, req) -> None:
    staff = req.staff
    send(db, 14, staff.account.email, f"Leave recorded for you: {_d(req.start_date)} to {_d(req.end_date)}", [
        f"HR has recorded {req.leave_type.name.lower()} for you from {_d(req.start_date)} to {_d(req.end_date)} ({_days(req.days)}).",
        *([f"Note from HR: {req.reason}"] if req.reason else []),
        "If this doesn't look right, reply to this email.",
    ], ("My leaves", f"{base}/staff/hr/leaves"), greeting=f"Hi {staff.first_name},")


# ── Attendance ────────────────────────────────────────────────────────────

def missed_check_in(db: Session, base: str, staff, day: date) -> None:
    send(db, 15, staff.account.email, "You haven't checked in today", [
        f"We don't have a check-in from you for today, {_d(day)}. Please check in from your staff portal.",
        "If you're on leave or away, apply for leave or tell HR so your attendance is correct.",
    ], ("Check in", f"{base}/staff/hr/attendance"), greeting=f"Hi {staff.first_name},")


def attendance_corrected(db: Session, base: str, staff, day: str, old: str, new: str, reason: str) -> None:
    send(db, 16, staff.account.email, f"Your attendance for {_d(day)} was updated", [
        f"HR has updated your attendance for {_d(day)} from {old} to {new}.",
        *([f"Reason: {reason}"] if reason else []),
        "If this doesn't look right, reply to this email.",
    ], ("My attendance", f"{base}/staff/hr/attendance"), greeting=f"Hi {staff.first_name},")


def daily_summary(db: Session, base: str, day: date, present: int, on_leave: int, missing: list[str]) -> None:
    send(db, 17, hr_emails(db), f"Attendance today: {present} present, {len(missing)} not checked in", [
        f"Attendance for {_d(day)}:",
        f"Present: {present}\nOn leave: {on_leave}\nNot checked in: {len(missing)}" + (f" ({', '.join(missing)})" if missing else ""),
    ], ("Open attendance", f"{base}/hr/attendance"))


# ── Pay and letters ───────────────────────────────────────────────────────

def payslip_ready(db: Session, base: str, doc) -> None:
    send(db, 18, doc.staff.account.email, f"Your payslip for {doc.data['month_label']} is ready", [
        f"Your payslip for {doc.data['month_label']} is ready. Sign in to your staff portal and open My payslips to view or print it.",
        "For your security, payslips are not attached to emails.",
    ], ("My payslips", f"{base}/staff/hr/payslips"), greeting=f"Hi {doc.staff.first_name},")


def payslips_finalised(db: Session, base: str, month_label: str, count: int, pay_date: str) -> None:
    send(db, 19, admin_emails(db), f"{month_label} payslips finalised", [
        f"HR has finalised the payslips for {month_label}.",
        f"Employees paid: {count}" + (f"\nPay date: {_d(pay_date)}" if pay_date else ""),
        "Each payslip is in the admin console (read-only).",
    ], ("HR records", f"{base}/admin/hr-records"))


def letter_finalised(db: Session, base: str, doc) -> None:
    """Emails that follow a finalised letter: salary revision (20), probation confirmed (21), exit letters (24)."""
    staff = doc.staff
    if not staff:
        return
    link = ("My documents", f"{base}/staff/hr/documents")
    if doc.kind == "SALARY_REVISION":
        send(db, 20, staff.account.email, "Your salary revision letter is ready", [
            f"Your salary has been revised with effect from {_d(doc.data.get('effective_date'))}. Your revision letter "
            f"({doc.document_number}) is in your staff portal under My documents.",
            "Congratulations, and thank you for your work.",
        ], link, greeting=f"Hi {staff.first_name},")
    elif doc.kind == "PROBATION" and doc.data.get("outcome") == "Confirmed":
        send(db, 21, staff.account.email, "Your employment is confirmed", [
            f"We're happy to confirm your employment as {doc.data.get('designation')} from {_d(doc.data.get('effective_date'))}. "
            f"Your confirmation letter ({doc.document_number}) is in your staff portal under My documents.",
        ], link, greeting=f"Hi {staff.first_name},")
    elif doc.kind in ("RELIEVING", "EXPERIENCE"):
        send(db, 24, staff.account.email, f"Your {doc.kind.lower()} letter from {settings.APP_NAME}", [
            f"Thank you for your time with {settings.APP_NAME}. Your {doc.kind.lower()} letter ({doc.document_number}) is below.",
            "We wish you every success.",
        ], letter_html=doc.rendered_html, greeting=f"Dear {staff.first_name},")


# ── HR documents on file ──────────────────────────────────────────────────

def documents_expiring(db: Session, base: str, rows: list[tuple[str, str, date]]) -> None:
    send(db, 22, hr_emails(db), f"{len(rows)} employee document{'s' if len(rows) != 1 else ''} expire in the next 30 days", [
        "These HR documents expire soon:",
        "\n".join(f"{name}: {doc_type}, expires {_d(expiry)}" for name, doc_type, expiry in rows),
        "Ask each employee for a renewed copy and upload it to their Documents tab.",
    ], ("Open HR workspace", f"{base}/hr/dashboard"))


def renewal_needed(db: Session, staff, doc_type: str, expiry: date | None, due: date) -> bool:
    return send(db, 23, staff.account.email, f"Please send a renewed copy of your {doc_type}", [
        f"Our copy of your {doc_type} " + (f"expires on {_d(expiry)}." if expiry else "needs renewing.")
        + f" Please share a renewed copy with HR by {_d(due)}.",
    ], greeting=f"Hi {staff.first_name},")

