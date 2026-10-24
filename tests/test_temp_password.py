"""A temporary password from an admin must be replaced at the first sign-in before anything else opens."""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.core.security import create_access_token, verify_password
from app.main import app
from app.modules.admin.models.admin import Admin
from app.modules.admin.services import accounts as admin_accounts
from app.platform.models.auth import AccountType, AuthAccount
from app.platform.models.lookups import TaxYear

from tests.test_auth_email import PASSWORD, _account, _token, env  # noqa: F401  (fixture)

NEW = "My0wn!pass"


def _admin(env) -> TestClient:
    db = env["Session"]()
    acc = AuthAccount(email="boss@x.test", password_hash="x", account_type=AccountType.ADMIN, is_active=True, is_verified=True)
    db.add(acc)
    db.flush()
    db.add(Admin(account_id=acc.id, first_name="Boss", last_name="A"))
    db.add(TaxYear(year=2025, name="2025", is_open=True, is_default=True, is_active=True, sort_order=1))
    db.commit()
    db.close()
    c = TestClient(app)
    c.cookies.update({"access_token": create_access_token("boss@x.test", "ADMIN")})
    return c


def _sign_in(email: str, password: str) -> tuple[TestClient, object]:
    c = TestClient(app)
    r = c.post("/auth/login", data={"username": email, "password": password}, follow_redirects=False)
    return c, r


def test_admin_created_client_must_set_own_password_first(env):
    made = _admin(env).post("/admin/quick-action/client", data={"first_name": "Nina", "last_name": "K", "email": "nina@x.test",
                                                                "phone": "+15551234567"}).json()
    temp = made["temp_password"]
    assert _account(env, "nina@x.test").must_change_password

    c, r = _sign_in("nina@x.test", temp)
    assert r.headers["location"].endswith("/auth/change-password")
    # Nothing else opens yet: pages show the "Set your own password" screen, fetch() gets the code
    page = c.get("/client/dashboard", headers={"accept": "text/html"})
    assert page.status_code == 403 and "Set my password" in page.text
    assert c.get("/chat/api/unread", headers={"accept": "application/json"}).json()["code"] == "AUTH-009"

    assert "Your current password is incorrect" in c.post("/auth/change-password", data={
        "current_password": "wrong", "new_password": NEW, "confirm_password": NEW}).text
    assert "different from your current one" in c.post("/auth/change-password", data={
        "current_password": temp, "new_password": temp, "confirm_password": temp}).text
    assert "Password must contain" in c.post("/auth/change-password", data={
        "current_password": temp, "new_password": "short", "confirm_password": "short"}).text

    r = c.post("/auth/change-password", data={"current_password": temp, "new_password": NEW, "confirm_password": NEW},
               follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith("/client/years")
    acc = _account(env, "nina@x.test")
    assert not acc.must_change_password and verify_password(NEW, acc.password_hash)
    assert c.get("/client/dashboard").status_code == 200
    assert c.get("/auth/change-password", follow_redirects=False).status_code == 303  # done: the page sends them home


def test_admin_reset_sets_the_flag_and_forgot_password_clears_it(env):
    _admin(env)
    db = env["Session"]()
    boss = db.query(AuthAccount).filter(AuthAccount.email == "boss@x.test").one()
    target = db.query(AuthAccount).filter(AuthAccount.email == "client@x.test").one()
    temp, err = admin_accounts.reset_password(db, boss.id, target)
    db.close()
    assert not err and _account(env, "client@x.test").must_change_password
    _, r = _sign_in("client@x.test", temp)
    assert r.headers["location"].endswith("/auth/change-password")

    # Choosing a password through Forgot password counts too
    TestClient(app).post("/auth/forgot-password", data={"email": "client@x.test"})
    token = _token(env["sent"][-1][3]["link"])
    TestClient(app).post("/auth/reset-password", data={"token": token, "new_password": NEW, "confirm_password": NEW})
    assert not _account(env, "client@x.test").must_change_password


def test_people_who_chose_their_own_password_are_not_asked(env):
    _, r = _sign_in("client@x.test", PASSWORD)
    assert "change-password" not in r.headers["location"]
