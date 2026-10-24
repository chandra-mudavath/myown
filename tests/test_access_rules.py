"""HR has no case access; wrong-password blocks are per IP; registration limits; admin-only staff accounts; access log."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.security import create_access_token, hash_password
from app.main import app
from app.modules.admin.models.admin import Admin
from app.modules.admin.services import accounts as admin_accounts
from app.modules.staff.models.staff import Staff, StaffRole
from app.platform.models.auth import AccountType, AuthAccount
from app.platform.models.system import AuditLog
from app.platform.models.tax_filing import TaxFiling
from app.platform.services import auth_service

from tests.test_auth_email import PASSWORD, _account, env  # noqa: F401  (fixture: client@x.test with PASSWORD)
from tests.test_client_forms import as_, env as forms_env  # noqa: F401


def _login(ip: str, password: str, email: str = "client@x.test"):
    c = TestClient(app, client=(ip, 5000))
    return c.post("/auth/login", data={"username": email, "password": password}, follow_redirects=False)


# ── HR ──────────────────────────────────────────────────────────────────────

def _add_hr(forms_env):
    db = forms_env["Session"]()
    acc = AuthAccount(email="hr@x.test", password_hash="x", account_type=AccountType.STAFF, is_active=True, is_verified=True)
    db.add(acc)
    db.flush()
    db.add(Staff(account_id=acc.id, first_name="Hema", last_name="R", role=StaffRole.HR))
    db.commit()
    db.close()
    c = TestClient(app)
    c.cookies.update({"access_token": create_access_token("hr@x.test", "STAFF")})
    return c


def test_hr_cannot_open_cases_clients_or_documents(forms_env):
    client = as_(forms_env, "client")
    client.post("/client/efile-authorization", data={"taxpayer_pin": "12345", "consent_efile": "on"})  # makes a case
    db = forms_env["Session"]()
    case_id = db.query(TaxFiling).one().id
    db.close()
    hr = _add_hr(forms_env)
    for url in ("/staff/cases", "/staff/cases/my", f"/staff/cases/{case_id}", f"/staff/cases/{case_id}/documents",
                "/staff/clients", "/staff/referrals", "/staff/notifications", f"/staff/cases/{case_id}/documents/x/file"):
        r = hr.get(url, headers={"accept": "application/json"})
        assert r.status_code == 403 and r.json()["code"] == "AUTH-008", url
    r = hr.post(f"/staff/cases/{case_id}/stage", data={"to": "pending_review"}, headers={"accept": "application/json"})
    assert r.status_code == 403
    # Their home is the profile; the sidebar has no case links
    assert hr.get("/staff/dashboard", follow_redirects=False).headers["location"] == "/staff/profile"
    page = hr.get("/staff/profile").text
    assert 'href="/staff/messages"' in page and 'href="/staff/referrals"' not in page and "Stages" not in page
    # Other staff still work as before
    assert as_(forms_env, "staff").get(f"/staff/cases/{case_id}").status_code == 200


# ── Wrong passwords ─────────────────────────────────────────────────────────

def test_wrong_passwords_block_only_that_ip(env):
    for _ in range(auth_service.MAX_FAILED_ATTEMPTS):
        _login("6.6.6.6", "Wrong!pass1")
    assert "Too many wrong passwords" in _login("6.6.6.6", PASSWORD).text  # even the right password, from that IP
    assert _login("10.0.0.1", PASSWORD).status_code == 303                  # the real person, elsewhere, gets in
    assert _account(env, "client@x.test").locked_until is None


def test_guesses_from_many_ips_lock_the_whole_account(env):
    for i in range(auth_service.ACCOUNT_LOCK_ATTEMPTS):
        _login(f"7.7.{i}.1", "Wrong!pass1")
    assert _account(env, "client@x.test").locked_until is not None
    assert "Too many wrong passwords" in _login("10.0.0.1", PASSWORD).text


def test_admin_unlock_and_password_reset_lift_the_blocks(env):
    for _ in range(auth_service.MAX_FAILED_ATTEMPTS):
        _login("6.6.6.6", "Wrong!pass1")
    db = env["Session"]()
    admin_acc = AuthAccount(email="boss@x.test", password_hash="x", account_type=AccountType.ADMIN, is_active=True, is_verified=True)
    db.add(admin_acc)
    db.flush()
    db.add(Admin(account_id=admin_acc.id, first_name="Boss", last_name="A"))
    db.commit()
    target = db.query(AuthAccount).filter(AuthAccount.email == "client@x.test").one()
    target.failed_login_count = 5  # what the admin page shows
    db.commit()
    assert admin_accounts.unlock(db, admin_acc.id, target) is None
    db.close()
    assert _login("6.6.6.6", PASSWORD).status_code == 303


def test_unknown_emails_are_blocked_the_same_way(env):
    for _ in range(auth_service.MAX_FAILED_ATTEMPTS):
        _login("6.6.6.6", "x", "ghost@x.test")
    assert "Too many wrong passwords" in _login("6.6.6.6", "x", "ghost@x.test").text  # no hint whether it exists


# ── Registration ────────────────────────────────────────────────────────────

def _register(ip: str, email: str):
    c = TestClient(app, client=(ip, 5000))
    return c.post("/auth/register", data={"first_name": "New", "last_name": "User", "email": email, "password": PASSWORD,
                                          "password_confirmation": PASSWORD, "country_code": "+1",
                                          "phone_local": "5551234567"}, follow_redirects=False)


def test_registrations_are_limited_per_ip(env):
    for i in range(auth_service.REGISTRATIONS_PER_IP_PER_HOUR):
        assert _register("8.8.8.8", f"new{i}@example.com").status_code == 303
    r = _register("8.8.8.8", "one-more@example.com")
    assert r.status_code == 429 and "Too many accounts" in r.text
    assert _register("9.9.9.9", "one-more@example.com").status_code == 303


def test_register_without_a_phone_shows_an_error_not_a_crash(env):
    c = TestClient(app)
    r = c.post("/auth/register", data={"first_name": "New", "email": "nophone@example.com", "password": PASSWORD,
                                       "password_confirmation": PASSWORD})
    assert r.status_code == 200 and "Enter your mobile number" in r.text


def test_staff_accounts_are_made_by_admins_only(env, monkeypatch):
    monkeypatch.setattr(settings, "STAFF_SELF_SIGNUP", False)
    r = _register("8.8.4.4", "ravi@covrenttax.com")
    assert "created by an administrator" in r.text
    db = env["Session"]()
    assert db.query(AuthAccount).filter(AuthAccount.email == "ravi@covrenttax.com").first() is None
    db.close()
    assert "created by an administrator" in TestClient(app).get("/auth/staff/register").text


# ── Access log ──────────────────────────────────────────────────────────────

def test_staff_views_of_case_details_are_logged(forms_env):
    client = as_(forms_env, "client")
    client.post("/client/efile-authorization", data={"taxpayer_pin": "12345", "consent_efile": "on"})
    db = forms_env["Session"]()
    case_id = db.query(TaxFiling).one().id
    db.close()
    as_(forms_env, "staff").get(f"/staff/cases/{case_id}")
    db = forms_env["Session"]()
    row = db.query(AuditLog).filter(AuditLog.action == "case_details_viewed").one()
    assert row.entity_id == case_id and row.ip_address
    db.close()
