"""Everything the employee page shows, tab by tab. Used by the HR workspace (editable) and by the
admin console's HR records (read-only), so both show the same record the same way."""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.modules.admin.services.accounts import lock_state
from app.modules.hr.services import documents, hr_settings, people, staff_files, time_off
from app.modules.staff.models.staff import Staff

TABS = [
    ("Overview", [("summary", "Summary"), ("personal", "Personal details")]),
    ("Employment", [("job", "Job details"), ("salary", "Salary history"), ("files", "Documents on file"), ("letters", "Letters")]),
    ("Time", [("attendance", "Attendance"), ("leaves", "Leaves")]),
    ("Pay", [("payslips", "Payslips")]),
    ("Account", [("access", "Sign-in and access")]),
]
TAB_NAMES = {key: label for _, tabs in TABS for key, label in tabs}


def context(db: Session, staff: Staff, tab: str, month: str = "") -> dict:
    tab = tab if tab in TAB_NAMES else "summary"
    record = people.employment_record(db, staff)
    manager = people.employee(db, record.reporting_manager_staff_id) if record and record.reporting_manager_staff_id else None
    ctx = {"employee": staff, "tab": tab, "tab_name": TAB_NAMES[tab], "tabs": TABS, "record": record, "manager": manager,
           "people": people, "picker": people.employee_picker(db), "money": documents.money, "doc_title": documents.title}
    if tab in ("summary", "leaves"):
        ctx["balances"] = time_off.balances(db, staff)
    if tab == "summary":
        t = time_off.today()
        ctx["month_totals"] = time_off.totals(time_off.month_codes(db, staff, t.year, t.month))
        ctx["files_count"] = len(staff_files_list(db, staff))
        last = documents.for_staff(db, staff, final_only=True, kinds=("PAYSLIP",))
        ctx["last_payslip"] = last[0] if last else None
    elif tab == "job":
        ctx.update(managers=people.managers(db, staff), employment_types=people.EMPLOYMENT_TYPES,
                   employment_statuses=people.EMPLOYMENT_STATUSES, payroll_fields=people.PAYROLL_FIELDS,
                   payroll=people.payroll_view(record, masked=False), payroll_masked=people.payroll_view(record))
    elif tab == "salary":
        ctx["salary_rows"] = people.salary_history(db, staff)
    elif tab == "files":
        ctx.update(files=staff_files_list(db, staff), file_types=staff_files.document_types(db))
    elif tab == "letters":
        ctx.update(letters=[d for d in documents.for_staff(db, staff) if d.kind != "PAYSLIP"], letter_kinds=documents.LETTER_KINDS)
    elif tab == "attendance":
        y, m = time_off.parse_month(month)
        codes = time_off.month_codes(db, staff, y, m)
        ctx.update(nav=time_off.month_nav(y, m), codes=codes, totals=time_off.totals(codes), code_labels=time_off.CODE_LABELS,
                   corrections=time_off.CORRECTIONS, week_offs=hr_settings.week_off_days(db))
    elif tab == "leaves":
        ctx.update(leave_requests=time_off.requests(db, staff), leave_types=hr_settings.leave_types(db, active_only=True))
    elif tab == "payslips":
        ctx["payslip_docs"] = documents.for_staff(db, staff, kinds=("PAYSLIP",))
    elif tab == "access":
        ctx["locked_until"] = lock_state(staff.account)
    return ctx


def staff_files_list(db: Session, staff: Staff):
    return people.documents(db, staff)
