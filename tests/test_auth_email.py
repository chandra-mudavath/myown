"""Auth emails: password reset and staff/admin email verification, against an in-memory SQLite database."""
from __future__ import annotations

import re
import smtplib

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.db_models  # noqa: F401
from app.core.config import settings
from app.core.database import Base, get_db
from app.core.security import hash_password
from app.main import app
from app.modules.client.models.client import Client
from app.platform.models.auth import AccountType, AuthAccount
from app.platform.models.lookups import AccountTypeLookup
from app.platform.services import email_service, login_otp

PASSWORD = "Str0ng!pass"


@pytest.fixture()
def env(monkeypatch):
    monkeypatch.setattr(settings, "STAFF_SELF_SIGNUP", True)  # these tests sign staff up themselves
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    event.listen(engine, "connect", lambda c, _: c.execute("PRAGMA foreign_keys=ON"))
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    def _db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _db
    db = Session()
    for i, (code, internal) in enumerate([("CLIENT", False), ("STAFF", True), ("ADMIN", True), ("HR", True)], 1):
        db.add(AccountTypeLookup(id=i, code=code, name=code.title(), number_prefix=code[:3], portal_path="/" + code.lower(),
                                 has_case_access=True, is_internal=internal, sort_order=i))
    acc = AuthAccount(email="client@x.test", password_hash=hash_password(PASSWORD), account_type=AccountType.CLIENT,
                      is_active=True, is_verified=True)
    db.add(acc)
    db.flush()
    db.add(Client(account_id=acc.id, first_name="Mike", last_name="T", phone="1"))
    db.commit()

    sent = []  # (to, subject, template, ctx) of every email the app tried to send
    monkeypatch.setattr(email_service, "send",
                        lambda to, subject, template, **ctx: sent.append((to, subject, template, ctx)) or True)
    yield {"Session": Session, "sent": sent}
    app.dependency_overrides.clear()
    db.close()


def _token(link: str) -> str:
    return re.search(r"token=([\w-]+)", link).group(1)


def _account(env, email):
    db = env["Session"]()
    try:
        return db.query(AuthAccount).filter(AuthAccount.email == email).one()
    finally:
        db.close()


# ── Forgot / reset password ─────────────────────────────────────────────────

def test_forgot_password_emails_a_link_instead_of_opening_the_reset_page(env):
    client = TestClient(app)
    r = client.post("/auth/forgot-password", data={"email": "client@x.test"}, follow_redirects=False)
    assert r.status_code == 200 and "sent it a link to reset the password" in r.text
    [(to, _, template, ctx)] = env["sent"]
    assert (to, template) == ("client@x.test", "password_reset")
    assert ctx["link"].startswith("http://testserver/auth/reset-password?token=")

    r = client.post("/auth/reset-password", data={"token": _token(ctx["link"]), "new_password": "N3w!password",
                                                  "confirm_password": "N3w!password"})
    assert "Password updated" in r.text
    r = client.post("/auth/login", data={"username": "client@x.test", "password": "N3w!password"}, follow_redirects=False)
    assert r.status_code == 303


def test_forgot_password_gives_the_same_answer_for_unknown_emails(env):
    r = TestClient(app).post("/auth/forgot-password", data={"email": "nobody@x.test"})
    assert r.status_code == 200 and "sent it a link to reset the password" in r.text
    assert env["sent"] == []


def test_reset_links_use_public_base_url(env, monkeypatch):
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "https://app.covrenttax.com/")
    TestClient(app).post("/auth/forgot-password", data={"email": "client@x.test"})
    assert env["sent"][0][3]["link"].startswith("https://app.covrenttax.com/auth/reset-password?token=")


# ── Staff / admin emailed codes ─────────────────────────────────────────────

STAFF = "initiator.dev@covrenttax.com"
LOGIN = {"username": STAFF, "password": PASSWORD}


def _register_staff(client):
    return client.post("/auth/register", data={
        "first_name": "Ravi", "last_name": "Kumar", "email": STAFF,
        "password": PASSWORD, "password_confirmation": PASSWORD, "signup_role": "staff",
        "country_code": "+91", "phone_local": "9876543210"}, follow_redirects=False)


def _last_code(env):
    to, subject, template, ctx = env["sent"][-1]
    assert template == "login_code" and ctx["code"] in subject
    return ctx["code"]


def _signed_up_staff(env):
    client = TestClient(app)
    _register_staff(client)
    client.post("/auth/verify-code", data={"code": _last_code(env)}, follow_redirects=False)
    client.cookies.clear()
    return client


def test_staff_sign_up_is_confirmed_with_an_emailed_code(env):
    client = TestClient(app)
    r = _register_staff(client)
    assert r.status_code == 303 and r.headers["location"].endswith("/auth/verify-code")
    assert _account(env, STAFF).is_verified is False
    [(to, _, _, ctx)] = env["sent"]
    assert (to, ctx["purpose"]) == (STAFF, "signup")

    page = client.get("/auth/verify-code")
    assert "Confirm your <span>email</span>" in page.text and "i***********v@covrenttax.com" in page.text
    r = client.post("/auth/verify-code", data={"code": ctx["code"]}, follow_redirects=False)
    assert r.status_code == 303 and "access_token" in r.cookies
    assert _account(env, STAFF).is_verified is True


def test_staff_sign_in_needs_the_code_after_the_password(env):
    client = _signed_up_staff(env)
    r = client.post("/auth/login", data=LOGIN, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith("/auth/verify-code")
    assert "access_token" not in r.cookies
    assert env["sent"][-1][3]["purpose"] == "login"

    r = client.post("/auth/verify-code", data={"code": "000000" if _last_code(env) != "000000" else "111111"})
    assert "isn&#39;t right. 4 tries left" in r.text
    r = client.post("/auth/verify-code", data={"code": _last_code(env)}, follow_redirects=False)
    assert r.status_code == 303 and "access_token" in r.cookies


def test_five_wrong_codes_end_the_attempt(env):
    client = _signed_up_staff(env)
    client.post("/auth/login", data=LOGIN)
    good = _last_code(env)
    wrong = "000000" if good != "000000" else "111111"
    for _ in range(4):
        client.post("/auth/verify-code", data={"code": wrong})
    r = client.post("/auth/verify-code", data={"code": wrong})
    assert "Sign in again to get a new one" in r.text
    r = client.post("/auth/verify-code", data={"code": good}, follow_redirects=False)
    assert "access_token" not in r.cookies


def test_a_new_code_needs_a_minute_and_replaces_the_old_one(env, monkeypatch):
    client = _signed_up_staff(env)
    client.post("/auth/login", data=LOGIN)
    old = _last_code(env)
    assert "wait a minute" in client.post("/auth/verify-code/resend").text

    monkeypatch.setattr(login_otp, "OTP_RESEND_SECONDS", 0)
    assert "sent a new code" in client.post("/auth/verify-code/resend").text
    new = _last_code(env)
    if new != old:
        assert "isn&#39;t right" in client.post("/auth/verify-code", data={"code": old}).text
    r = client.post("/auth/verify-code", data={"code": new}, follow_redirects=False)
    assert r.status_code == 303 and "access_token" in r.cookies


def test_wrong_password_sends_no_code(env):
    client = _signed_up_staff(env)
    sent = len(env["sent"])
    client.post("/auth/login", data={"username": STAFF, "password": "Wr0ng!pass"})
    assert len(env["sent"]) == sent


def test_clients_sign_in_without_a_code(env):
    r = TestClient(app).post("/auth/login", data={"username": "client@x.test", "password": PASSWORD}, follow_redirects=False)
    assert r.status_code == 303 and "access_token" in r.cookies
    assert env["sent"] == []


def test_code_page_without_a_pending_code_goes_to_sign_in(env):
    r = TestClient(app).get("/auth/verify-code", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith("/auth/login")


# ── Sending ──────────────────────────────────────────────────────────────────

class _FakeSMTP:
    sent: list = []

    def __init__(self, host, port, **kwargs):
        self.host, self.port = host, port

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def login(self, user, password):
        self.user = user

    def send_message(self, msg):
        _FakeSMTP.sent.append((self.host, self.port, self.user, msg))


def test_smtp_backend_sends_from_no_reply_with_support_as_reply_to(monkeypatch):
    _FakeSMTP.sent = []
    for key, value in {"EMAIL_BACKEND": "smtp", "SMTP_HOST": "smtp.resend.com", "SMTP_PORT": 465,
                       "SMTP_USER": "resend", "SMTP_PASSWORD": "key"}.items():
        monkeypatch.setattr(settings, key, value)
    monkeypatch.setattr(smtplib, "SMTP_SSL", _FakeSMTP)
    assert email_service.send_password_reset("a@x.test", "https://app.covrenttax.com/r?token=abc", 15) is True
    [(host, port, user, msg)] = _FakeSMTP.sent
    assert (host, port, user) == ("smtp.resend.com", 465, "resend")
    assert msg["From"] == "CovrentTax <no-reply@covrenttax.com>"
    assert msg["Reply-To"] == "support@covrenttax.com"
    assert "https://app.covrenttax.com/r?token=abc" in msg.get_body(("plain",)).get_content()
    assert 'href="https://app.covrenttax.com/r?token=abc"' in msg.get_body(("html",)).get_content()


def test_console_backend_never_opens_a_connection(monkeypatch):
    monkeypatch.setattr(settings, "EMAIL_BACKEND", "console")
    monkeypatch.setattr(smtplib, "SMTP", lambda *a, **k: pytest.fail("console mode must not connect"))
    assert email_service.send_login_code("a@x.test", "123456", "login", 10) is True


def test_a_failed_send_is_logged_not_raised(monkeypatch):
    monkeypatch.setattr(settings, "EMAIL_BACKEND", "smtp")
    monkeypatch.setattr(settings, "SMTP_PORT", 587)

    def refuse(*a, **k):
        raise ConnectionRefusedError("no server")
    monkeypatch.setattr(smtplib, "SMTP", refuse)
    assert email_service.send_login_code("a@x.test", "123456", "login", 10) is False


# ── Celery email tasks ────────────────────────────────────────────────────────

def test_email_task_runs_in_process_when_eager_and_does_not_retry(monkeypatch):
    from app.core.celery_app import celery_app
    from app.platform.tasks import email_tasks
    monkeypatch.setattr(celery_app.conf, "task_always_eager", True)
    calls = []
    monkeypatch.setattr(email_service, "send_login_code", lambda *a: calls.append(a) or False)
    email_tasks.enqueue(email_tasks.send_login_code, "a@x.test", "123456", "login", 10)
    assert calls == [("a@x.test", "123456", "login", 10)]  # failed once, logged, no retry loop


def test_email_is_sent_inline_when_the_queue_is_down(monkeypatch):
    from kombu.exceptions import OperationalError
    from app.platform.tasks import email_tasks

    def broker_down(*a, **k):
        raise OperationalError("redis down")
    monkeypatch.setattr(email_tasks.send_password_reset, "delay", broker_down)
    calls = []
    monkeypatch.setattr(email_service, "send_password_reset", lambda *a: calls.append(a) or True)
    email_tasks.enqueue(email_tasks.send_password_reset, "a@x.test", "https://x/r", 15)
    assert calls == [("a@x.test", "https://x/r", 15)]
