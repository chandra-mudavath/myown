"""HR documents from fixed templates: payslips, offer letters and the other letters.

One template per kind (templates/hr/letters/<kind>.html). HR fills a short form; known data is
pre-filled from the employee's record. A document is a draft until HR finalises it: then the
rendered page is stored and never changes, even if the template is edited later. The same
rendered page is shown on screen, printed (Print / Save as PDF), downloaded as a PDF and placed in emails.
"""
from __future__ import annotations

import base64
import calendar
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from sqlalchemy.orm import Session

from app.core.templates import plain_number, templates
from app.modules.hr.models.records import HrDocument
from app.modules.hr.services import hr_settings, people, time_off
from app.modules.staff.models.staff import Staff
from app.modules.staff.models.staff_records import StaffEmploymentDetails, StaffSalaryHistory
from app.platform.models.system import AuditLog

TWO = Decimal("0.01")

# Background watermark of every HR document: always "CovrentTax" (not the editable company name), large,
# faint and diagonal. One tile is about one A4 page tall, so on screen it repeats once per page; in print
# and in the PDF it is fixed to each page.
_WATERMARK_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="720" height="1000" viewBox="0 0 720 1000">'
    '<text x="360" y="500" text-anchor="middle" dominant-baseline="middle" transform="rotate(-32 360 500)" '
    'font-family="Arial, Helvetica, sans-serif" font-weight="800" font-size="112" letter-spacing="-2" '
    'fill="#2563eb" fill-opacity="0.06">CovrentTax</text></svg>')
WATERMARK = "data:image/svg+xml;base64," + base64.b64encode(_WATERMARK_SVG.encode()).decode()


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    kind: str = "text"        # text | textarea | date | number | select
    filled_by: str = "hr"     # hr | auto (pre-filled, HR can still correct it in a draft)
    required: bool = True
    options: tuple = ()


@dataclass(frozen=True)
class Kind:
    code: str
    label: str
    for_candidate: bool
    fields: tuple[Field, ...]


_EMPLOYMENT = tuple((label, label) for _, label in people.EMPLOYMENT_TYPES)
KINDS: dict[str, Kind] = {k.code: k for k in (
    Kind("OFFER", "Offer letter", True, (
        Field("candidate_name", "Candidate name"),
        Field("candidate_email", "Candidate's personal email"),
        Field("candidate_address", "Candidate address", "textarea", required=False),
        Field("designation", "Designation"),
        Field("department", "Department"),
        Field("reporting_manager", "Reporting manager", required=False),
        Field("work_location", "Work location"),
        Field("employment_type", "Employment type", "select", options=_EMPLOYMENT),
        Field("joining_date", "Date of joining", "date"),
        Field("reporting_time", "Reporting time on the first day", required=False),
        Field("monthly_salary", "Monthly salary (gross)", "number"),
        Field("probation_months", "Probation (months)", "number"),
        Field("notice_days", "Notice period after probation (days)", "number"),
        Field("accept_by", "Accept by", "date"),
    )),
    Kind("APPOINTMENT", "Appointment letter", False, (
        Field("employee_name", "Employee name", filled_by="auto"),
        Field("employee_number", "Employee number", filled_by="auto"),
        Field("designation", "Designation", filled_by="auto"),
        Field("department", "Department", filled_by="auto"),
        Field("joining_date", "Date of joining", "date", filled_by="auto"),
        Field("monthly_salary", "Monthly salary (gross)", "number", filled_by="auto"),
        Field("probation_months", "Probation (months)", "number"),
        Field("notice_days", "Notice period (days)", "number"),
    )),
    Kind("SALARY_REVISION", "Salary revision letter", False, (
        Field("employee_name", "Employee name", filled_by="auto"),
        Field("employee_number", "Employee number", filled_by="auto"),
        Field("designation", "Designation", filled_by="auto"),
        Field("previous_salary", "Previous monthly salary", "number", filled_by="auto"),
        Field("monthly_salary", "New monthly salary", "number", filled_by="auto"),
        Field("effective_date", "Effective from", "date", filled_by="auto"),
        Field("reason", "Reason", "select", options=(("Annual review", "Annual review"), ("Promotion", "Promotion"),
                                                      ("Correction", "Correction"))),
        Field("new_designation", "New designation (promotion only)", required=False),
    )),
    Kind("PROBATION", "Probation confirmation letter", False, (
        Field("employee_name", "Employee name", filled_by="auto"),
        Field("employee_number", "Employee number", filled_by="auto"),
        Field("designation", "Designation", filled_by="auto"),
        Field("joining_date", "Date of joining", "date", filled_by="auto"),
        Field("outcome", "Outcome", "select", options=(("Confirmed", "Confirmed"), ("Extended", "Probation extended"))),
        Field("effective_date", "Confirmed from / extended until", "date"),
        Field("reason", "Reason (if extended)", "textarea", required=False),
        Field("notice_days", "Notice period after confirmation (days)", "number"),
    )),
    Kind("RELIEVING", "Relieving letter", False, (
        Field("employee_name", "Employee name", filled_by="auto"),
        Field("employee_number", "Employee number", filled_by="auto"),
        Field("designation", "Designation", filled_by="auto"),
        Field("joining_date", "Date of joining", "date", filled_by="auto"),
        Field("resignation_date", "Resignation date", "date"),
        Field("last_working_day", "Last working day", "date", filled_by="auto"),
        Field("settlement", "Full and final settlement", "select", options=(("Completed", "Completed"), ("In progress", "In progress"))),
    )),
    Kind("EXPERIENCE", "Experience letter", False, (
        Field("employee_name", "Employee name", filled_by="auto"),
        Field("designation", "Designation", filled_by="auto"),
        Field("joining_date", "Date of joining", "date", filled_by="auto"),
        Field("last_working_day", "Last working day", "date", filled_by="auto"),
        Field("role_summary", "What they did (two or three lines)", "textarea"),
        Field("conduct", "Conduct", "select", options=(("good", "Good"), ("very good", "Very good"), ("excellent", "Excellent"))),
    )),
)}
LETTER_KINDS = [k for k in KINDS.values() if not k.for_candidate]
PAYSLIP_EXTRAS = (("bonus", "Bonus", "earning"), ("arrears", "Arrears", "earning"), ("reimbursement", "Reimbursement", "earning"),
                  ("tds", "Income tax (TDS)", "deduction"), ("advance", "Advance recovery", "deduction"))


def _audit(db: Session, actor_id: str | None, action: str, doc: HrDocument, old=None, new=None) -> None:
    db.add(AuditLog(actor_account_id=actor_id, action=action, entity_type="hr_document", entity_id=doc.id,
                    old_values=old, new_values=new))


def money(value) -> str:
    try:
        return f"{Decimal(str(value)).quantize(TWO, ROUND_HALF_UP):,.2f}"
    except (InvalidOperation, ValueError):
        return str(value or "")


def _dec(value, default: str = "0") -> Decimal:
    try:
        return Decimal(str(value).replace(",", "").strip() or default)
    except InvalidOperation:
        return Decimal(default)


_ONES = ["", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten", "Eleven", "Twelve",
         "Thirteen", "Fourteen", "Fifteen", "Sixteen", "Seventeen", "Eighteen", "Nineteen"]
_TENS = ["", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety"]


def _below_100(n: int) -> str:
    return _ONES[n] if n < 20 else (_TENS[n // 10] + (" " + _ONES[n % 10] if n % 10 else ""))


def _below_1000(n: int) -> str:
    return (_ONES[n // 100] + " Hundred" + (" " + _below_100(n % 100) if n % 100 else "")) if n >= 100 else _below_100(n)


def in_words(amount: Decimal) -> str:
    """Indian numbering: 1,25,000 → One Lakh Twenty Five Thousand."""
    n = int(amount.quantize(Decimal(1), ROUND_HALF_UP))
    if n == 0:
        return "Zero"
    parts = []
    for size, name in ((10_000_000, "Crore"), (100_000, "Lakh"), (1_000, "Thousand")):
        if n >= size:
            parts.append(f"{_below_1000(n // size) if size == 10_000_000 else _below_100(n // size)} {name}")
            n %= size
    if n:
        parts.append(_below_1000(n))
    return " ".join(parts) + " only"


def breakup(db: Session, monthly: Decimal) -> list[tuple[str, Decimal]]:
    basic = (monthly * hr_settings.number(db, "basic_pct") / 100).quantize(TWO, ROUND_HALF_UP)
    hra = (monthly * hr_settings.number(db, "hra_pct") / 100).quantize(TWO, ROUND_HALF_UP)
    return [("Basic", basic), ("HRA", hra), ("Special allowance", monthly - basic - hra)]


def _pf(db: Session, basic: Decimal) -> Decimal:
    """Provident fund on a basic salary: PF % of basic, up to the monthly limit (same for employee and employer)."""
    pf = (basic * hr_settings.number(db, "pf_pct") / 100).quantize(TWO, ROUND_HALF_UP)
    cap = hr_settings.number(db, "pf_cap")
    return min(pf, cap) if cap > 0 else pf


def package(db: Session, monthly: Decimal) -> dict:
    """The offer letter's package page: earnings, employer PF, cost to company, usual deductions and take-home."""
    earnings = breakup(db, monthly)
    pf = _pf(db, earnings[0][1])
    pt = hr_settings.number(db, "professional_tax").quantize(TWO)
    ctc = monthly + pf
    take_home = monthly - pf - pt
    return {"earnings": earnings, "gross": monthly, "employer_pf": pf, "ctc": ctc, "employee_pf": pf, "pt": pt,
            "take_home": take_home, "pf_pct": hr_settings.number(db, "pf_pct"), "pf_cap": hr_settings.number(db, "pf_cap")}


def _fmt_date(value: str | None) -> str:
    try:
        return date.fromisoformat(value).strftime("%d %b %Y") if value else ""
    except ValueError:
        return value or ""


# ── Defaults for a new document ───────────────────────────────────────────

def _employee_defaults(db: Session, staff: Staff) -> dict:
    record = people.employment_record(db, staff)
    latest = db.query(StaffSalaryHistory).filter(StaffSalaryHistory.staff_id == staff.id).order_by(
        StaffSalaryHistory.effective_date.desc(), StaffSalaryHistory.created_at.desc()).first()
    return {
        "employee_name": people.full_name(staff),
        "employee_number": record.employee_number if record else staff.staff_number,
        "designation": (record.designation if record else None) or staff.job_title or "",
        "department": record.department if record and record.department else "",
        "joining_date": record.date_of_joining.isoformat() if record and record.date_of_joining else "",
        "last_working_day": record.termination_date.isoformat() if record and record.termination_date else "",
        "monthly_salary": str(record.current_salary) if record and record.current_salary is not None else "",
        "previous_salary": str(latest.previous_salary) if latest and latest.previous_salary is not None else "",
        "effective_date": latest.effective_date.isoformat() if latest else "",
        "probation_months": "6", "notice_days": "30", "conduct": "good",
    }


def defaults(db: Session, kind: Kind, staff: Staff | None, prefill: dict | None = None) -> dict:
    data = {"probation_months": "6", "notice_days": "30", "work_location": "Hyderabad office",
            "employment_type": "Full time", "reporting_time": "10:00 AM"} if kind.for_candidate else _employee_defaults(db, staff)
    data.update({k: v for k, v in (prefill or {}).items() if v})
    return {f.key: str(data.get(f.key, "") or "") for f in kind.fields}


def _clean(kind: Kind, form: dict) -> tuple[dict, str | None]:
    data = {}
    for f in kind.fields:
        value = " ".join(str(form.get(f.key, "")).split()) if f.kind != "textarea" else str(form.get(f.key, "")).strip()
        value = value[:1000]
        if f.required and not value and not (kind.code == "PROBATION" and f.key == "reason"):
            return data, f"{f.label} is required."
        if value and f.kind == "date":
            try:
                date.fromisoformat(value)
            except ValueError:
                return data, f"{f.label}: enter a date."
        if value and f.kind == "number":
            if _dec(value, "-1") < 0:
                return data, f"{f.label}: enter a number."
            value = str(_dec(value))
        if value and f.kind == "select" and value not in {o[0] for o in f.options}:
            return data, f"Choose {f.label.lower()}."
        data[f.key] = value
    if kind.code == "OFFER" and "@" not in data.get("candidate_email", ""):
        return data, "Enter the candidate's email address."
    return data, None


# ── Letters (and offers) ────────────────────────────────────────────────────

def get(db: Session, doc_id: str) -> HrDocument | None:
    return db.get(HrDocument, doc_id)


def create(db: Session, actor_id: str, kind_code: str, staff: Staff | None, form: dict) -> tuple[HrDocument | None, str | None]:
    kind = KINDS.get(kind_code)
    if not kind:
        return None, "Choose a document."
    if not kind.for_candidate and not staff:
        return None, "Choose an employee."
    data, err = _clean(kind, form)
    if err:
        return None, err
    doc = HrDocument(kind=kind.code, staff_id=staff.id if staff else None, status="draft", data=data,
                     created_by_account_id=actor_id)
    db.add(doc)
    db.flush()
    _audit(db, actor_id, "hr_document_created", doc, None, {"kind": kind.code})
    db.commit()
    return doc, None


def update(db: Session, actor_id: str, doc: HrDocument, form: dict) -> str | None:
    if doc.status != "draft":
        return "Only a draft can be changed."
    if doc.kind == "PAYSLIP":
        return update_payslip(db, actor_id, doc, form)
    data, err = _clean(KINDS[doc.kind], form)
    if err:
        return err
    old, doc.data = dict(doc.data), data
    _audit(db, actor_id, "hr_document_updated", doc, old, data)
    db.commit()
    return None


def render(db: Session, doc: HrDocument) -> str:
    """The document itself (letterhead to signature), as HTML. Final documents use their stored copy."""
    if doc.status == "final" and doc.rendered_html:
        return doc.rendered_html
    kind = doc.kind.lower()
    extra = {}
    if doc.kind == "OFFER":
        joining = doc.data.get("joining_date") or ""
        year = int(joining[:4]) if joining[:4].isdigit() else time_off.today().year
        offs = hr_settings.week_off_days(db)
        extra = {"package": package(db, _dec(doc.data.get("monthly_salary"))),
                 "leave_types": [lt for lt in hr_settings.leave_types(db, active_only=True)],
                 "holidays": time_off.holidays(db, year), "holiday_year": year,
                 "week_offs": [label for i, (_, label) in enumerate(hr_settings.WEEKDAYS) if i in offs],
                 "lines": lambda text: [line.strip() for line in (text or "").splitlines() if line.strip()]}
    return templates.get_template(f"hr/letters/{kind}.html").render(
        doc=doc, d=doc.data, company=hr_settings.get_all(db), money=money, fmt_date=_fmt_date, in_words=in_words,
        breakup=breakup(db, _dec(doc.data.get("monthly_salary"))) if doc.kind != "PAYSLIP" else [],
        kind=KINDS.get(doc.kind), draft=doc.status == "draft", watermark=WATERMARK, **extra)


def finalise(db: Session, actor_id: str, doc: HrDocument) -> str | None:
    if doc.status != "draft":
        return "This document is already final."
    doc.status = "final"  # render the final version (no DRAFT mark), then keep it
    doc.rendered_html = render(db, doc)
    doc.finalised_by_account_id, doc.finalised_at = actor_id, datetime.now(timezone.utc)
    _audit(db, actor_id, "hr_document_finalised", doc, {"status": "draft"}, {"status": "final"})
    db.commit()
    return None


def void(db: Session, actor_id: str, doc: HrDocument) -> str | None:
    if doc.status == "void":
        return "This document is already void."
    old = doc.status
    doc.status = "void"
    _audit(db, actor_id, "hr_document_voided", doc, {"status": old}, {"status": "void"})
    db.commit()
    return None


def for_staff(db: Session, staff: Staff, final_only: bool = False, kinds: tuple[str, ...] | None = None) -> list[HrDocument]:
    q = db.query(HrDocument).filter(HrDocument.staff_id == staff.id)
    q = q.filter(HrDocument.status == "final") if final_only else q.filter(HrDocument.status != "void")
    if kinds:
        q = q.filter(HrDocument.kind.in_(kinds))
    return q.order_by(HrDocument.created_at.desc()).all()


def title(doc: HrDocument) -> str:
    if doc.kind == "PAYSLIP":
        y, m = (int(p) for p in doc.period.split("-"))
        return f"Payslip · {date(y, m, 1).strftime('%B %Y')}"
    who = doc.data.get("candidate_name") or doc.data.get("employee_name") or ""
    return f"{KINDS[doc.kind].label}{' · ' + who if who else ''}"


# ── PDF download ────────────────────────────────────────────────────────────

def print_page(db: Session, doc: HrDocument) -> str:
    """The document on its own page (the Print / Save as PDF page), which is also what goes into the PDF."""
    return templates.get_template("hr/letters/print.html").render(letter=render(db, doc), title=title(doc), doc=doc)


def pdf_filename(doc: HrDocument) -> str:
    who = doc.data.get("candidate_name") or doc.data.get("employee_name") or ""
    parts = [doc.document_number or "document", KINDS[doc.kind].label if doc.kind in KINDS else doc.kind.title(), who]
    if doc.kind == "PAYSLIP" and doc.period:
        parts.insert(2, doc.period)
    name = "_".join(re.sub(r"[^A-Za-z0-9-]+", "-", p).strip("-") for p in parts if p)
    return f"{name or 'document'}.pdf"


def pdfs(db: Session, docs: list[HrDocument]) -> list[tuple[str, bytes]]:
    """(filename, PDF bytes) per document. Raises pdf.PdfUnavailable when the server can't make PDFs."""
    from app.platform.services.pdf import html_to_pdfs
    files = html_to_pdfs([print_page(db, d) for d in docs])
    return [(pdf_filename(d), data) for d, data in zip(docs, files)]


# ── Offers ──────────────────────────────────────────────────────────────────

OFFER_STATUSES = {"sent": "Sent", "accepted": "Accepted", "declined": "Declined", "withdrawn": "Withdrawn"}


def offers(db: Session, status: str = "") -> list[HrDocument]:
    q = db.query(HrDocument).filter(HrDocument.kind == "OFFER")
    if status == "draft":
        q = q.filter(HrDocument.status == "draft")
    elif status in OFFER_STATUSES:
        q = q.filter(HrDocument.offer_status == status)
    return q.order_by(HrDocument.created_at.desc()).all()


def set_offer_status(db: Session, actor_id: str, doc: HrDocument, status: str) -> str | None:
    if doc.kind != "OFFER" or doc.status != "final":
        return "Finalise the offer letter first."
    allowed = {None: {"sent"}, "sent": {"accepted", "declined", "withdrawn"}, "accepted": {"withdrawn"}}
    if status not in allowed.get(doc.offer_status, set()):
        return "That change isn't possible for this offer."
    old = doc.offer_status
    doc.offer_status = status
    if status == "sent":
        doc.emailed_at = datetime.now(timezone.utc)
    _audit(db, actor_id, "offer_status_changed", doc, {"offer_status": old}, {"offer_status": status})
    db.commit()
    return None


def link_offer_to_staff(db: Session, actor_id: str, doc: HrDocument, staff: Staff) -> None:
    """Accepted offer → the new staff member: job details and first salary row from the offer."""
    doc.staff_id = staff.id
    record = people.employment_record(db, staff)
    if not record:
        joined = doc.data.get("joining_date")
        types = {label: code for code, label in people.EMPLOYMENT_TYPES}
        record = StaffEmploymentDetails(
            staff_id=staff.id, designation=doc.data.get("designation") or None, department=doc.data.get("department") or None,
            date_of_joining=date.fromisoformat(joined) if joined else None,
            employment_type=types.get(doc.data.get("employment_type")), employment_status="PROBATION",
            updated_by_account_id=actor_id)
        db.add(record)
        db.flush()
        salary = _dec(doc.data.get("monthly_salary"))
        if salary > 0:
            db.add(StaffSalaryHistory(staff_id=staff.id, effective_date=record.date_of_joining or date.today(),
                                      previous_salary=None, new_salary=salary, reason="Joining (offer " + doc.document_number + ")",
                                      approved_by_account_id=actor_id))
            record.current_salary = salary
    _audit(db, actor_id, "offer_linked_to_staff", doc, None, {"staff_id": staff.id})
    db.commit()


# ── Payslips ────────────────────────────────────────────────────────────────

def period_of(year: int, month: int) -> str:
    return f"{year:04d}-{month:02d}"


def payslips(db: Session, period: str) -> list[HrDocument]:
    return (db.query(HrDocument).filter(HrDocument.kind == "PAYSLIP", HrDocument.period == period, HrDocument.status != "void")
            .order_by(HrDocument.created_at).all())


def _payslip_data(db: Session, staff: Staff, record: StaffEmploymentDetails, year: int, month: int, extras: dict) -> dict:
    n = calendar.monthrange(year, month)[1]
    first, last = date(year, month, 1), date(year, month, n)
    codes = time_off.month_codes(db, staff, year, month)
    lop = Decimal(0)
    not_employed = 0
    for i, code in enumerate(codes):
        d = first.replace(day=i + 1)
        if (record.date_of_joining and d < record.date_of_joining) or (record.termination_date and d > record.termination_date):
            not_employed += 1
        elif code in ("A", "UL"):
            lop += 1
        elif code == "HALF":
            lop += Decimal("0.5")
    gross_full = record.current_salary or Decimal(0)
    per_day = gross_full / n
    payable_days = Decimal(n - not_employed) - lop
    factor = payable_days / n if n else Decimal(0)
    rates = breakup(db, gross_full)
    earned = [(label, (amount * factor).quantize(TWO, ROUND_HALF_UP)) for label, amount in rates]
    lop_amount = sum((a for _, a in rates), Decimal(0)) - sum((a for _, a in earned), Decimal(0))
    earnings = list(earned)
    for key, label, side in PAYSLIP_EXTRAS:
        if side == "earning" and _dec(extras.get(key)) > 0:
            earnings.append((label, _dec(extras.get(key)).quantize(TWO)))
    pf = _pf(db, earned[0][1])
    pf_deductions = [(f"Employee PF ({plain_number(hr_settings.number(db, 'pf_pct'))}% of basic)", pf)] if pf > 0 else []
    other = []
    pt = hr_settings.number(db, "professional_tax")
    if pt > 0 and payable_days > 0:
        other.append(("Professional tax", pt.quantize(TWO)))
    for key, label, side in PAYSLIP_EXTRAS:
        if side == "deduction" and _dec(extras.get(key)) > 0:
            other.append((label, _dec(extras.get(key)).quantize(TWO)))
    deductions = pf_deductions + other
    gross = sum((a for _, a in earnings), Decimal(0))
    total_ded = sum((a for _, a in deductions), Decimal(0))
    net = max(gross - total_ded, Decimal(0))
    t = time_off.totals(codes)
    holidays = [h for h in time_off.holidays(db, year) if first <= h.day <= last]
    working_days = n - t["W"] - t["H"]
    ids = people.payroll_view(record)
    return {
        "employee_name": people.full_name(staff), "employee_number": record.employee_number,
        "designation": record.designation or "", "department": record.department or "",
        "joining_date": record.date_of_joining.isoformat() if record.date_of_joining else "",
        "email": staff.account.email, "month_label": first.strftime("%B %Y"),
        "period_from": first.isoformat(), "period_to": last.isoformat(),
        "pan": ids["pan"], "uan": ids["uan"], "pf_number": ids["pf_number"], "bank_name": ids["bank_name"],
        "bank_account": ids["bank_account"], "ifsc": ids["ifsc"],
        "days_in_month": n, "working_days": working_days, "not_employed_days": not_employed,
        "lop_days": plain_number(lop), "paid_days": plain_number(payable_days),
        "present": plain_number(t["P"] + t["HALF"] * 0.5), "half_days": t["HALF"],
        "leave_days": plain_number(t["L"] + t["HL"] * 0.5), "unpaid_leave": t["UL"], "absent": t["A"],
        "holidays": t["H"], "holiday_list": [[h.day.isoformat(), h.name] for h in holidays], "week_offs": t["W"],
        "monthly_salary": str(gross_full), "per_day": str(per_day.quantize(TWO)),
        "rate_earnings": [[l, str(a)] for l, a in rates], "lop_amount": str(lop_amount.quantize(TWO)),
        "earnings": [[l, str(a)] for l, a in earnings],
        "pf_deductions": [[l, str(a)] for l, a in pf_deductions], "other_deductions": [[l, str(a)] for l, a in other],
        "deductions": [[l, str(a)] for l, a in deductions], "employer_pf": str(pf),
        "gross": str(gross), "total_deductions": str(total_ded), "net": str(net), "net_words": in_words(net),
        "extras": {k: str(_dec(extras.get(k))) for k, _, _ in PAYSLIP_EXTRAS},
        "pay_date": extras.get("pay_date") or last.isoformat(), "pay_mode": extras.get("pay_mode") or "Bank transfer",
        "currency": hr_settings.get(db, "currency"),
    }


def generate_payslips(db: Session, actor_id: str, year: int, month: int) -> tuple[int, list[str]]:
    """Make or refresh the month's drafts for everyone with a salary. Finals are never touched.
    Returns (drafts made or refreshed, names skipped with the reason)."""
    period, made, skipped = period_of(year, month), 0, []
    first, last = date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])
    existing = {d.staff_id: d for d in payslips(db, period)}
    for staff in people.staff_query(db).order_by(Staff.first_name):
        record = people.employment_record(db, staff)
        name = people.full_name(staff)
        if not record or not record.current_salary:
            if staff.account.is_active:
                skipped.append(f"{name} (no salary yet)")
            continue
        if (record.date_of_joining and record.date_of_joining > last) or (record.termination_date and record.termination_date < first):
            continue
        doc = existing.get(staff.id)
        if doc and doc.status == "final":
            continue
        extras = dict(doc.data.get("extras", {}), pay_date=doc.data.get("pay_date"), pay_mode=doc.data.get("pay_mode")) if doc else {}
        data = _payslip_data(db, staff, record, year, month, extras)
        if doc:
            doc.data, doc.net_amount = data, Decimal(data["net"])
        else:
            doc = HrDocument(kind="PAYSLIP", staff_id=staff.id, period=period, status="draft", data=data,
                             net_amount=Decimal(data["net"]), created_by_account_id=actor_id)
            db.add(doc)
        made += 1
    db.add(AuditLog(actor_account_id=actor_id, action="payslips_generated", entity_type="hr_document", entity_id=period,
                    old_values=None, new_values={"period": period, "drafts": made}))
    db.commit()
    return made, skipped


def update_payslip(db: Session, actor_id: str, doc: HrDocument, form: dict) -> str | None:
    extras = {}
    for key, label, _ in PAYSLIP_EXTRAS:
        value = str(form.get(key, "") or "0").replace(",", "").strip()
        if _dec(value, "-1") < 0:
            return f"{label}: enter an amount."
        extras[key] = value
    pay_date = str(form.get("pay_date", "")).strip()
    try:
        date.fromisoformat(pay_date)
    except ValueError:
        return "Enter the pay date."
    extras["pay_date"], extras["pay_mode"] = pay_date, " ".join(str(form.get("pay_mode", "")).split())[:40] or "Bank transfer"
    staff, record = doc.staff, people.employment_record(db, doc.staff)
    y, m = (int(p) for p in doc.period.split("-"))
    old = dict(doc.data.get("extras", {}))
    doc.data = _payslip_data(db, staff, record, y, m, extras)
    doc.net_amount = Decimal(doc.data["net"])
    _audit(db, actor_id, "payslip_updated", doc, old, doc.data["extras"])
    db.commit()
    return None


def finalise_payslips(db: Session, actor_id: str, period: str) -> list[HrDocument]:
    done = []
    for doc in payslips(db, period):
        if doc.status == "draft" and not finalise(db, actor_id, doc):
            done.append(doc)
    return done
