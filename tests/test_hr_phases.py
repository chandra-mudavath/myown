"""HR phases 4–9: leaves, attendance, payslips, letters and offers, HR files, emails, staff self-service
and the admin's read-only HR records."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.core.security import create_access_token
from app.main import app
from app.modules.hr.models.records import AttendanceDay, HrDocument, LeaveRequest, LeaveType
from app.modules.hr.services import documents, hr_settings, time_off
from app.modules.staff.models.staff import Staff
from app.modules.staff.models.staff_records import StaffDocument, StaffEmploymentDetails
from app.platform.models.auth import AccountType, AuthAccount
from sqlalchemy import text

from tests.test_admin_creates_hr import _new_staff
from tests.test_auth_email import _account, env  # noqa: F401  (fixture)
from tests.test_hr_portal import _hr_client, _staff_id


@pytest.fixture()
def office(env, monkeypatch):
    """HR, admin and one staff member (Ravi) with job details and a 60,000 monthly salary.
    'Today' is pinned to Wed 18 Nov 2026 so week offs and month maths are predictable."""
    monkeypatch.setattr(time_off, "today", lambda: date(2026, 11, 18))
    hr, admin = _hr_client(env)
    _new_staff(admin, "ravi@covrenttax.com", "PREPARER")
    sid = _staff_id(env, "ravi@covrenttax.com")
    hr.get("/hr/dashboard")  # first HR page adds the default leave types and HR document types
    hr.post(f"/hr/employees/{sid}/job", data={"designation": "Tax Preparer", "department": "Tax", "date_of_joining": "2025-01-06",
                                               "employment_type": "FULL_TIME", "employment_status": "ACTIVE"})
    hr.post(f"/hr/employees/{sid}/salary", data={"new_salary": "60000", "effective_date": "2025-01-06", "reason": "Joining"})
    db = env["Session"]()
    db.query(AuthAccount).filter(AuthAccount.email == "ravi@covrenttax.com").update({"must_change_password": False})
    db.commit()
    db.close()
    staff = TestClient(app)
    staff.cookies.update({"access_token": create_access_token("ravi@covrenttax.com", "STAFF")})
    return {"hr": hr, "admin": admin, "staff": staff, "sid": sid, "env": env}


def _type_id(env, code):
    db = env["Session"]()
    try:
        return db.query(LeaveType.id).filter(LeaveType.code == code).scalar()
    finally:
        db.close()


def _emails(env, template="hr_notice"):
    return [(to, subject) for to, subject, tpl, _ in env["sent"] if tpl == template]


# ── Leaves ──────────────────────────────────────────────────────────────────

def test_leave_request_flow_with_balances_and_emails(office):
    env, hr, staff, sid = office["env"], office["hr"], office["staff"], office["sid"]
    casual = _type_id(env, "CASUAL")
    # Mon 23 – Fri 27 Nov: 5 working days
    r = staff.post("/staff/hr/leaves", data={"leave_type_id": casual, "start_date": "2026-11-23", "end_date": "2026-11-27",
                                             "reason": "Family function"}, follow_redirects=False)
    assert "saved=" in r.headers["location"]
    db = env["Session"]()
    req = db.query(LeaveRequest).one()
    db.close()
    assert req.status == "pending" and req.days == 5
    assert ("hr.asha@covrenttax.com", "Leave request: Hema R, Casual leave, 23 Nov 2026 to 27 Nov 2026") in _emails(env)
    # Overlapping dates are refused; so is more than the balance (12 a year, 5 asked)
    assert "error=" in staff.post("/staff/hr/leaves", data={"leave_type_id": casual, "start_date": "2026-11-25"},
                                  follow_redirects=False).headers["location"]
    assert "error=" in staff.post("/staff/hr/leaves", data={"leave_type_id": casual, "start_date": "2026-12-01",
                                                            "end_date": "2026-12-11"}, follow_redirects=False).headers["location"]
    # Rejecting needs a note; approving works and emails the staff member
    assert "error=" in hr.post(f"/hr/leaves/{req.id}/decide", data={"decision": "reject"}, follow_redirects=False).headers["location"]
    hr.post(f"/hr/leaves/{req.id}/decide", data={"decision": "approve"})
    assert ("ravi@covrenttax.com", "Your leave from 23 Nov 2026 to 27 Nov 2026 is approved") in _emails(env)
    page = staff.get("/staff/hr/leaves").text
    assert "Approved" in page and ">7<" in page  # 12 − 5 left
    # Approved leave shows on the attendance calendar
    db = env["Session"]()
    codes = time_off.month_codes(db, db.get(Staff, sid), 2026, 11)
    db.close()
    assert codes[22:27] == ["L"] * 5 and codes[28] == "W"


def test_carry_over_and_week_offs_holidays_not_counted(office):
    env, hr, sid = office["env"], office["hr"], office["sid"]
    hr.post("/hr/settings/holidays", data={"day": "2026-11-24", "name": "Test holiday"})
    db = env["Session"]()
    staff = db.get(Staff, sid)
    assert time_off.working_days(db, date(2026, 11, 23), date(2026, 11, 29)) == 4  # Tue holiday, Sat/Sun off
    earned = db.query(LeaveType).filter(LeaveType.code == "EARNED").one()
    # 2025: used 5 of 15 earned → 10 carried into 2026 (limit 30)
    db.add(LeaveRequest(staff_id=sid, leave_type_id=earned.id, start_date=date(2025, 3, 3), end_date=date(2025, 3, 7),
                        days=Decimal(5), status="approved"))
    db.commit()
    bal = {b.leave_type.code: b for b in time_off.balances(db, staff, 2026)}
    db.close()
    assert bal["EARNED"].carried == 10 and bal["EARNED"].left == 25 and bal["CASUAL"].carried == 0
    assert bal["UNPAID"].left is None  # no limit


# ── Attendance ──────────────────────────────────────────────────────────────

def test_check_in_out_and_hr_correction(office, monkeypatch):
    env, hr, staff, sid = office["env"], office["hr"], office["staff"], office["sid"]
    assert "Check in" in staff.get("/staff/hr/attendance").text
    staff.post("/staff/hr/check-in")
    assert "error=" in staff.post("/staff/hr/check-in", follow_redirects=False).headers["location"]  # only once
    staff.post("/staff/hr/check-out")
    db = env["Session"]()
    row = db.query(AttendanceDay).one()
    codes = time_off.month_codes(db, db.get(Staff, sid), 2026, 11)
    db.close()
    assert row.check_in_at and row.check_out_at and codes[17] == "P"
    assert codes[16] == "A" and codes[18] == ""  # yesterday not marked = absent; tomorrow not yet

    hr_settings_on = "1,4,9,11,12,16,18"
    db = env["Session"]()
    hr_settings.save(db, _account(env, "hr.asha@covrenttax.com").id, {**hr_settings.get_all(db), "emails_on": hr_settings_on,
                                                                       "week_off_SAT": "1", "week_off_SUN": "1"})
    db.close()
    r = hr.post("/hr/attendance/correct", data={"staff_id": sid, "day": "2026-11-17", "status": "P", "note": "Forgot to check in"},
                follow_redirects=False)
    assert "saved=" in r.headers["location"]
    assert ("ravi@covrenttax.com", "Your attendance for 17 Nov 2026 was updated") in _emails(env)
    assert "error=" in hr.post("/hr/attendance/correct", data={"staff_id": sid, "day": "2026-11-20", "status": "P"},
                               follow_redirects=False).headers["location"]  # no future days
    page = hr.get("/hr/attendance?month=2026-11").text
    assert "Hema R" in page and 'data-day="2026-11-17"' in page


# ── Payslips ────────────────────────────────────────────────────────────────

def test_payslip_drafts_lop_extras_and_finalise(office):
    env, hr, staff, sid = office["env"], office["hr"], office["staff"], office["sid"]
    db = env["Session"]()
    for d in range(1, 31):  # November: every working day present except 2 absences (left unmarked)
        day = date(2026, 11, d)
        if day.weekday() < 5 and d not in (10, 11):
            db.add(AttendanceDay(staff_id=sid, day=day, status="P"))
    db.commit()
    db.close()
    r = hr.post("/hr/payslips/generate", data={"month": "2026-11"}, follow_redirects=False)
    assert "1+draft" in r.headers["location"]
    db = env["Session"]()
    doc = db.query(HrDocument).filter(HrDocument.kind == "PAYSLIP").one()
    db.close()
    assert doc.document_number.startswith("PS-") and doc.status == "draft"
    # 2 absent + the rest of the month after "today" (18 Nov) counts as payable, so LOP = 2 days of 30
    assert doc.data["lop_days"] == "2" and doc.data["paid_days"] == "28"
    assert Decimal(doc.data["gross"]) == Decimal("56000.00")  # 60,000 × 28 / 30
    # One-off items, then regenerate keeps them
    hr.post(f"/hr/documents/{doc.id}", data={"bonus": "5000", "tds": "1200", "pay_date": "2026-11-30", "pay_mode": "Bank transfer",
                                             "arrears": "", "reimbursement": "", "advance": ""})
    hr.post("/hr/payslips/generate", data={"month": "2026-11"})
    db = env["Session"]()
    doc = db.get(HrDocument, doc.id)
    db.close()
    assert ["Bonus", "5000.00"] in doc.data["earnings"] and ["Income tax (TDS)", "1200.00"] in doc.data["deductions"]
    # Staff can't see a draft
    assert staff.get(f"/staff/hr/documents/{doc.id}", headers={"accept": "application/json"}).status_code == 404
    hr.post("/hr/payslips/finalise", data={"month": "2026-11"})
    db = env["Session"]()
    doc = db.get(HrDocument, doc.id)
    db.close()
    assert doc.status == "final" and "DRAFT" not in doc.rendered_html and "Net pay" in doc.rendered_html
    assert ("ravi@covrenttax.com", "Your payslip for November 2026 is ready") in _emails(env)
    assert "November 2026" in staff.get("/staff/hr/payslips").text
    assert "Net pay" in staff.get(f"/staff/hr/documents/{doc.id}").text
    assert "Print / Save as PDF" in staff.get(f"/staff/hr/documents/{doc.id}/print").text
    # A final payslip never changes
    assert "error=" in hr.post(f"/hr/documents/{doc.id}", data={"bonus": "1", "pay_date": "2026-11-30"},
                               follow_redirects=False).headers["location"]


def test_amount_in_words():
    assert documents.in_words(Decimal("125000")) == "One Lakh Twenty Five Thousand only"
    assert documents.in_words(Decimal("12345678")) == "One Crore Twenty Three Lakh Forty Five Thousand Six Hundred Seventy Eight only"


# ── Offers and letters ──────────────────────────────────────────────────────

OFFER = {"kind": "OFFER", "candidate_name": "Meena Shah", "candidate_email": "meena@example.com", "candidate_address": "Hyderabad",
         "designation": "Tax Preparer", "department": "Tax", "reporting_manager": "Hema R", "work_location": "Hyderabad office",
         "employment_type": "Full time", "joining_date": "2026-12-01", "reporting_time": "10:00 AM", "monthly_salary": "45000",
         "probation_months": "6", "notice_days": "30", "accept_by": "2026-11-25"}


def test_offer_to_new_staff_member(office):
    env, hr = office["env"], office["hr"]
    r = hr.post("/hr/documents", data=OFFER, follow_redirects=False)
    doc_id = r.headers["location"].split("/hr/documents/")[1].split("?")[0]
    page = hr.get(f"/hr/documents/{doc_id}").text
    assert "DRAFT" in page and "Meena Shah" in page and "45,000.00" in page
    hr.post(f"/hr/documents/{doc_id}/finalise")
    hr.post(f"/hr/documents/{doc_id}/offer", data={"status": "sent"})
    sent = [(to, s) for to, s in _emails(env) if to == "meena@example.com"]
    assert sent == [("meena@example.com", "Offer of employment: Tax Preparer at CovrentTax")]
    letter = [ctx for to, _, tpl, ctx in env["sent"] if to == "meena@example.com"][0]["letter_html"]
    assert "Offer of employment: Tax Preparer" in letter and "DRAFT" not in letter
    hr.post(f"/hr/documents/{doc_id}/offer", data={"status": "accepted"})
    assert "Meena" in hr.get(f"/hr/employees/new?offer={doc_id}").text
    r = hr.post("/hr/employees", data={"first_name": "Meena", "last_name": "Shah", "email": "meena@covrenttax.com",
                                       "role": "INITIATOR", "offer": doc_id})
    assert r.status_code == 200
    sid = _staff_id(env, "meena@covrenttax.com")
    db = env["Session"]()
    rec = db.query(StaffEmploymentDetails).filter(StaffEmploymentDetails.staff_id == sid).one()
    doc = db.get(HrDocument, doc_id)
    db.close()
    assert rec.designation == "Tax Preparer" and rec.current_salary == 45000 and rec.date_of_joining == date(2026, 12, 1)
    assert doc.staff_id == sid


def test_letter_from_record_and_staff_sees_only_final(office):
    env, hr, staff, sid = office["env"], office["hr"], office["staff"], office["sid"]
    form = hr.get(f"/hr/documents/new?kind=APPOINTMENT&staff_id={sid}").text
    assert 'value="Tax Preparer"' in form and 'value="60000.00"' in form  # pre-filled from the record
    r = hr.post("/hr/documents", data={"kind": "APPOINTMENT", "staff_id": sid, "employee_name": "Hema R", "employee_number": "EMP-000001",
                                       "designation": "Tax Preparer", "department": "Tax", "joining_date": "2025-01-06",
                                       "monthly_salary": "60000", "probation_months": "6", "notice_days": "30"}, follow_redirects=False)
    doc_id = r.headers["location"].split("/hr/documents/")[1].split("?")[0]
    assert "Nothing here yet" in staff.get("/staff/hr/documents").text
    hr.post(f"/hr/documents/{doc_id}/finalise")
    assert "Appointment letter" in staff.get("/staff/hr/documents").text
    # Other staff can't open it
    _new_staff(office["admin"], "kiran@covrenttax.com", "PREPARER")
    db = env["Session"]()
    db.query(AuthAccount).filter(AuthAccount.email == "kiran@covrenttax.com").update({"must_change_password": False})
    db.commit()
    db.close()
    other = TestClient(app)
    other.cookies.update({"access_token": create_access_token("kiran@covrenttax.com", "STAFF")})
    assert other.get(f"/staff/hr/documents/{doc_id}", headers={"accept": "application/json"}).status_code == 404


# ── Files, admin view, access ───────────────────────────────────────────────

def test_hr_files_upload_download_and_admin_read_only(office):
    env, hr, admin, sid = office["env"], office["hr"], office["admin"], office["sid"]
    r = hr.post(f"/hr/employees/{sid}/files", data={"document_type": "HR_ID_PROOF", "expiry_date": "2026-12-01"},
                files={"file": ("id.pdf", b"%PDF-1.4 test", "application/pdf")}, follow_redirects=False)
    assert "saved=" in r.headers["location"]
    db = env["Session"]()
    f = db.query(StaffDocument).one()
    db.close()
    assert f.document_number.startswith("SDOC-") and f.file_path.startswith("private_storage/staff_documents/")
    assert hr.get(f"/hr/files/{f.id}").content == b"%PDF-1.4 test"  # stored encrypted, sent back decrypted
    assert "error=" in hr.post(f"/hr/employees/{sid}/files", data={"document_type": "HR_ID_PROOF"},
                               files={"file": ("x.exe", b"MZ", "application/octet-stream")}, follow_redirects=False).headers["location"]

    # Admin: read-only view of the same record, no forms that change it
    assert "Hema R" in admin.get("/admin/hr-records").text
    page = admin.get(f"/admin/hr-records/{sid}?tab=salary").text
    assert "60,000.00" in page and "Add salary change" not in page
    assert admin.get(f"/admin/hr-records/files/{f.id}").content == b"%PDF-1.4 test"
    assert "Save job details" not in admin.get(f"/admin/hr-records/{sid}?tab=job").text
    # Staff and HR can't open the admin view; staff can't open the HR workspace
    assert office["staff"].get("/admin/hr-records", headers={"accept": "application/json"}).json()["code"] == "AUTH-006"
    assert hr.get("/admin/hr-records", headers={"accept": "application/json"}).json()["code"] == "AUTH-006"
    assert office["staff"].get(f"/hr/files/{f.id}", headers={"accept": "application/json"}).json()["code"] == "AUTH-010"


def test_settings_emails_toggle_and_leave_types(office):
    env, hr = office["env"], office["hr"]
    page = hr.get("/hr/settings").text
    assert "Casual leave" in page and "Missed check-in reminder" in page
    form = {**hr_settings.DEFAULTS, "company_address": "1 Main Road, Hyderabad", "week_off_SUN": "1", "email_9": "1"}
    form.pop("emails_on")
    hr.post("/hr/settings", data=form)
    db = env["Session"]()
    assert hr_settings.emails_on(db) == {9} and hr_settings.week_off_days(db) == {6}
    assert hr_settings.get(db, "company_address") == "1 Main Road, Hyderabad"
    db.close()
    hr.post("/hr/settings/leave-types", data={"name": "Maternity leave", "days_per_year": "180", "is_paid": "1"})
    assert "Maternity leave" in hr.get("/hr/settings").text
    assert "error=" in hr.post("/hr/settings", data={**form, "basic_pct": "80", "hra_pct": "30"}, follow_redirects=False).headers["location"]


def test_scheduled_jobs(office, monkeypatch):
    from app.platform.tasks import hr_tasks
    env = office["env"]
    monkeypatch.setattr(hr_tasks, "SessionLocal", env["Session"])
    db = env["Session"]()
    hr_settings.save(db, _account(env, "hr.asha@covrenttax.com").id,
                     {**hr_settings.get_all(db), "emails_on": ",".join(str(n) for n in range(1, 25)), "week_off_SAT": "1", "week_off_SUN": "1"})
    db.close()
    assert hr_tasks.daily_summary() == 0  # nobody checked in
    assert any(s.startswith("Attendance today: 0 present") for _, s in _emails(env))
    assert hr_tasks.documents_expiring() == 0
    assert isinstance(hr_tasks.offer_emails(), int)


def test_staff_stay_staff_accounts(office):
    assert _account(office["env"], "ravi@covrenttax.com").account_type == AccountType.STAFF


# ── Offer pages, payroll details and the detailed payslip ──────────────────

def test_payroll_details_validated_encrypted_and_masked(office):
    env, hr, sid = office["env"], office["hr"], office["sid"]
    bad = hr.post(f"/hr/employees/{sid}/payroll", data={"pan": "12345"}, follow_redirects=False)
    assert "error=" in bad.headers["location"]
    hr.post(f"/hr/employees/{sid}/payroll", data={"pan": "abcde1234f", "uan": "100200300400", "pf_number": "TS/HYD/12345/678",
                                                  "bank_name": "HDFC Bank", "bank_account": "50100123456789", "ifsc": "hdfc0001234"})
    db = env["Session"]()
    rec = db.query(StaffEmploymentDetails).filter(StaffEmploymentDetails.staff_id == sid).one()
    raw = db.execute(text("SELECT payroll_ids FROM staff_employment_details WHERE staff_id = :s"), {"s": sid}).scalar()
    db.close()
    assert rec.payroll_ids["pan"] == "ABCDE1234F" and rec.payroll_ids["ifsc"] == "HDFC0001234"
    assert "ABCDE1234F" not in str(raw) and "50100123456789" not in str(raw)  # encrypted in the database
    page = office["admin"].get(f"/admin/hr-records/{sid}?tab=job").text
    assert "ABCDE****F" in page and "****6789" in page and "50100123456789" not in page


def test_offer_has_company_pages_and_package_last(office):
    env, hr = office["env"], office["hr"]
    hr.post("/hr/settings/holidays", data={"day": "2026-12-25", "name": "Christmas"})
    r = hr.post("/hr/documents", data=OFFER, follow_redirects=False)
    doc_id = r.headers["location"].split("/hr/documents/")[1].split("?")[0]
    page = hr.get(f"/hr/documents/{doc_id}").text
    order = ["About us", "How we work", "What we expect from everyone", "Your first day", "Leave and holidays", "Salary package", "Acceptance"]
    positions = [page.rindex(t) for t in order]  # the last match: page 1 lists the same titles
    assert positions == sorted(positions)
    assert "Page 5 of 5" in page and "Casual leave" in page and "Christmas" in page
    assert "Cost to company" in page and "Employer PF" in page and "Estimated take-home" in page
    assert "22,500.00" in page and "1,800.00" in page  # basic 50% of 45,000; employer PF capped at 1,800


def test_payslip_shows_pf_employee_and_month_details(office):
    env, hr, sid = office["env"], office["hr"], office["sid"]
    hr.post(f"/hr/employees/{sid}/payroll", data={"pan": "ABCDE1234F", "uan": "100200300400", "pf_number": "TS/HYD/12345/678",
                                                  "bank_name": "HDFC Bank", "bank_account": "50100123456789", "ifsc": "HDFC0001234"})
    form = {**hr_settings.DEFAULTS, "company_pf_code": "TSHYD1234567000", "week_off_SAT": "1", "week_off_SUN": "1"}
    form.pop("emails_on")
    hr.post("/hr/settings", data=form)
    hr.post("/hr/settings/holidays", data={"day": "2026-11-09", "name": "Diwali"})
    hr.post("/hr/payslips/generate", data={"month": "2026-11"})
    db = env["Session"]()
    doc = db.query(HrDocument).filter(HrDocument.kind == "PAYSLIP").one()
    db.close()
    page = hr.get(f"/hr/documents/{doc.id}").text
    for text_ in ("TSHYD1234567000", "ABCDE****F", "100200300400", "TS/HYD/12345/678", "****6789", "Working days", "Diwali",
                  "PF deductions", "Other deductions", "Employee PF (12% of basic)", "Monthly rate", "Total salary (monthly rate)",
                  "Gross earnings this month", "Employer PF contribution"):
        assert text_ in page, text_
    assert doc.data["working_days"] == 20  # 30 days − 9 week offs − 1 holiday
