"""HR workspace: HR signs in like other company accounts and lands on the HR dashboard; it lists every
employee, opens one employee with their tabs, edits job details and salary, creates and deactivates
staff accounts; HR never reaches staff/admin/client pages or case conversations."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.core.security import create_access_token
from app.main import app
from app.modules.staff.models.staff import Staff
from app.modules.staff.models.staff_records import StaffEmploymentDetails, StaffSalaryHistory
from app.platform.models.auth import AccountType, AuthAccount
from app.platform.services import chat_service

from tests.test_admin_creates_hr import _new_staff, _roles
from tests.test_auth_email import _account, _last_code, env  # noqa: F401  (fixture)
from tests.test_temp_password import _admin

NEW = "N3w!Passw0rd"


def _hr_client(env, email="hr.asha@covrenttax.com") -> tuple[TestClient, TestClient]:
    """(HR, admin) clients."""
    _roles(env)
    admin = _admin(env)
    _new_staff(admin, email, "HR")
    db = env["Session"]()
    acc = db.query(AuthAccount).filter(AuthAccount.email == email).one()
    acc.must_change_password = False
    db.commit()
    db.close()
    c = TestClient(app)
    c.cookies.update({"access_token": create_access_token(email, "HR")})
    return c, admin


def _staff_id(env, email) -> str:
    db = env["Session"]()
    try:
        return db.query(Staff.id).join(AuthAccount, AuthAccount.id == Staff.account_id).filter(AuthAccount.email == email).scalar()
    finally:
        db.close()


def test_hr_signs_in_with_code_and_lands_on_the_hr_dashboard(env):
    _roles(env)
    temp = _new_staff(_admin(env), "hr.asha@covrenttax.com", "HR").json()["temp_password"]
    c = TestClient(app)
    r = c.post("/auth/login", data={"username": "hr.asha@covrenttax.com", "password": temp}, follow_redirects=False)
    assert r.headers["location"].endswith("/auth/verify-code")  # step two: the emailed code
    r = c.post("/auth/verify-code", data={"code": _last_code(env)}, follow_redirects=False)
    assert r.headers["location"].endswith("/auth/change-password")  # temporary password first
    r = c.post("/auth/change-password", data={"current_password": temp, "new_password": NEW, "confirm_password": NEW},
               follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith("/hr/dashboard")
    page = c.get("/hr/dashboard").text
    assert "All employees" in page and "HR workspace" in page


def test_dashboard_lists_employees_and_opens_one_with_sidebar_tabs(env):
    hr, admin = _hr_client(env)
    _new_staff(admin, "ravi@covrenttax.com", "PREPARER")
    sid = _staff_id(env, "ravi@covrenttax.com")
    page = hr.get("/hr/dashboard").text
    assert "Total employees" in page and f'/hr/employees/{sid}' in page
    assert "hr.asha@" not in page  # HR people aren't employees in this list

    page = hr.get(f"/hr/employees/{sid}").text
    assert "All employees" in page and 'data-hr-picker' in page
    for tab in ("summary", "personal", "job", "salary", "files", "letters", "attendance", "leaves", "payslips", "access"):
        assert f"/hr/employees/{sid}?tab={tab}" in page
        assert hr.get(f"/hr/employees/{sid}?tab={tab}").status_code == 200
    assert hr.get("/hr/employees/not-a-staff-id", headers={"accept": "application/json"}).status_code == 404


def test_job_details_then_add_only_salary_history(env):
    hr, admin = _hr_client(env)
    _new_staff(admin, "ravi@covrenttax.com", "PREPARER")
    sid = _staff_id(env, "ravi@covrenttax.com")
    # Salary needs job details first
    r = hr.post(f"/hr/employees/{sid}/salary", data={"new_salary": "50000", "effective_date": "2026-11-01"}, follow_redirects=False)
    assert "error=" in r.headers["location"]

    r = hr.post(f"/hr/employees/{sid}/job", data={"designation": "Tax Preparer", "department": "Tax preparation",
                                                   "date_of_joining": "2026-11-01", "employment_type": "FULL_TIME",
                                                   "employment_status": "ACTIVE"}, follow_redirects=False)
    assert "saved=" in r.headers["location"]
    assert "error=" in hr.post(f"/hr/employees/{sid}/job", data={"employment_status": "TERMINATED"},
                               follow_redirects=False).headers["location"]  # needs a last working day

    hr.post(f"/hr/employees/{sid}/salary", data={"new_salary": "50000", "effective_date": "2026-11-01", "reason": "Joining"})
    hr.post(f"/hr/employees/{sid}/salary", data={"new_salary": "55,000", "effective_date": "2027-04-01", "reason": "Review"})
    db = env["Session"]()
    record = db.query(StaffEmploymentDetails).filter(StaffEmploymentDetails.staff_id == sid).one()
    rows = db.query(StaffSalaryHistory).filter(StaffSalaryHistory.staff_id == sid).order_by(StaffSalaryHistory.effective_date).all()
    db.close()
    assert record.employee_number.startswith("EMP-") and record.current_salary == 55000
    assert [(r.previous_salary, r.new_salary, r.hike_percentage) for r in rows] == [(None, 50000, None), (50000, 55000, 10)]


def test_hr_creates_and_deactivates_staff_but_not_hr(env):
    hr, admin = _hr_client(env)
    r = hr.post("/hr/employees", data={"first_name": "Kiran", "last_name": "M", "email": "kiran@covrenttax.com", "role": "INITIATOR"})
    assert r.status_code == 200 and "shown only this once" in r.text and r.headers["cache-control"] == "no-store"
    assert _account(env, "kiran@covrenttax.com").account_type == AccountType.STAFF
    assert hr.post("/hr/employees", data={"first_name": "X", "email": "x.hr@covrenttax.com", "role": "HR"}).status_code == 400

    sid = _staff_id(env, "kiran@covrenttax.com")
    hr.post(f"/hr/employees/{sid}/deactivate")
    assert not _account(env, "kiran@covrenttax.com").is_active
    hr.post(f"/hr/employees/{sid}/activate")
    assert _account(env, "kiran@covrenttax.com").is_active


def test_hr_is_kept_out_of_other_portals_and_case_chats(env):
    hr, admin = _hr_client(env)
    for url, code in (("/staff/dashboard", "AUTH-005"), ("/admin/dashboard", "AUTH-006"), ("/client/dashboard", "AUTH-004")):
        assert hr.get(url, headers={"accept": "application/json"}).json()["code"] == code, url
    assert hr.get("/hr/messages").status_code == 200

    db = env["Session"]()
    acc = db.query(AuthAccount).filter(AuthAccount.email == "hr.asha@covrenttax.com").one()
    viewer = chat_service.viewer_for(db, acc)
    assert viewer.internal and not viewer.works_cases  # direct and group chats yes, client/case chats no
    db.close()

    # Other portals' people can't open the HR workspace
    _new_staff(admin, "ravi@covrenttax.com", "PREPARER")
    staff = TestClient(app)
    staff.cookies.update({"access_token": create_access_token("ravi@covrenttax.com", "STAFF")})
    db = env["Session"]()
    db.query(AuthAccount).filter(AuthAccount.email == "ravi@covrenttax.com").update({"must_change_password": False})
    db.commit()
    db.close()
    assert staff.get("/hr/dashboard", headers={"accept": "application/json"}).json()["code"] == "AUTH-010"
