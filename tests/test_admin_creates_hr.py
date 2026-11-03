"""Staff sign-up is off, so admins create HR accounts too (Nov 2026), from + New Staff and the role menus."""
from __future__ import annotations

from sqlalchemy import text

from app.modules.staff.models.staff import Staff, StaffRole
from app.platform.models.case_workflow import CaseAssignment
from app.platform.models.lookups import StaffRoleLookup

from tests.test_auth_email import _account, env  # noqa: F401  (fixture)
from tests.test_temp_password import _admin


def _roles(env):
    db = env["Session"]()
    for i, code in enumerate(("INITIATOR", "PREPARER", "REVIEWER", "MANAGER"), 1):
        db.add(StaffRoleLookup(id=i, code=code, name=code.title(), is_case_role=True, is_active=True, sort_order=i))
    db.commit()
    db.close()


def _new_staff(admin, email, role):
    return admin.post("/admin/quick-action/staff", data={"first_name": "Hema", "last_name": "R", "email": email, "role": role})


def _staff(env, email):
    db = env["Session"]()
    try:
        return db.query(Staff).filter(Staff.account_id == _account(env, email).id).one()
    finally:
        db.close()


def test_admin_creates_an_hr_account(env):
    _roles(env)
    admin = _admin(env)
    assert {"code": "HR", "name": "HR"} in admin.get("/admin/api/form-options").json()["staff_roles"]

    r = _new_staff(admin, "hr.test1@covrenttax.com", "HR")
    assert r.status_code == 200 and r.json()["temp_password"]
    assert _staff(env, "hr.test1@covrenttax.com").role == StaffRole.HR
    assert _account(env, "hr.test1@covrenttax.com").must_change_password

    # Only a case role or HR: never ADMIN, never an unknown code
    assert _new_staff(admin, "sneaky@covrenttax.com", "ADMIN").status_code == 400
    assert _new_staff(admin, "typo@covrenttax.com", "BOSS").status_code == 400


def test_role_menus_offer_hr_but_not_while_on_cases(env):
    _roles(env)
    admin = _admin(env)
    _new_staff(admin, "preparer.test1@covrenttax.com", "PREPARER")
    staff = _staff(env, "preparer.test1@covrenttax.com")
    assert 'value="HR"' in admin.get(f"/admin/staff/{staff.id}").text
    assert 'value="HR"' in admin.get("/admin/staff").text

    # On a case team: HR (no case access) is refused until they're removed from it
    db = env["Session"]()
    db.execute(text("PRAGMA foreign_keys=OFF"))  # a bare assignment row; the case itself doesn't matter here
    db.add(CaseAssignment(case_id="case-1", staff_id=staff.id, role_id=2, status="active"))
    db.commit()
    db.execute(text("PRAGMA foreign_keys=ON"))
    db.close()
    r = admin.post(f"/admin/staff/{staff.id}/role", data={"role_code": "HR"}, follow_redirects=False)
    assert "error=" in r.headers["location"] and "HR+has+no+access+to+cases" in r.headers["location"]
    assert _staff(env, "preparer.test1@covrenttax.com").role == StaffRole.PREPARER


def test_change_role_to_hr(env):
    _roles(env)
    admin = _admin(env)
    _new_staff(admin, "reviewer.test1@covrenttax.com", "REVIEWER")
    staff = _staff(env, "reviewer.test1@covrenttax.com")
    r = admin.post(f"/admin/staff/{staff.id}/role", data={"role_code": "HR"}, follow_redirects=False)
    assert r.status_code == 303 and "error" not in r.headers["location"]
    assert _staff(env, "reviewer.test1@covrenttax.com").role == StaffRole.HR
