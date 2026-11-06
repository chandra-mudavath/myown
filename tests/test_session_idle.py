"""Idle timeout: working keeps the sign-in alive; doing nothing for SESSION_IDLE_MINUTES signs out (Nov 2026)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import jwt
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.security import decode_token
from app.main import app

from tests.test_auth_email import PASSWORD, env  # noqa: F401  (fixture)


def _signed_in() -> TestClient:
    c = TestClient(app)
    r = c.post("/auth/login", data={"username": "client@x.test", "password": PASSWORD}, follow_redirects=False)
    assert r.status_code == 303 and c.cookies.get("refresh_token")
    return c


def _age(c: TestClient, minutes_left: float) -> None:
    """Pretend the person has been idle: the access cookie now has only this long left."""
    exp = datetime.now(timezone.utc) + timedelta(minutes=minutes_left)
    c.cookies.set("access_token", jwt.encode({"sub": "client@x.test", "type": "access", "account_type": "CLIENT",
                                              "exp": exp}, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM))


def _renewed(r) -> float | None:
    """Minutes left on the access cookie the response set, or None if it didn't renew it."""
    token = r.cookies.get("access_token")
    if not token:
        return None
    return (decode_token(token)["exp"] - datetime.now(timezone.utc).timestamp()) / 60


def test_sign_in_lasts_the_idle_window(env):
    c = _signed_in()
    left = (decode_token(c.cookies.get("access_token"))["exp"] - datetime.now(timezone.utc).timestamp()) / 60
    assert settings.SESSION_IDLE_MINUTES - 1 < left <= settings.SESSION_IDLE_MINUTES


def test_working_renews_the_sign_in(env):
    c = _signed_in()
    _age(c, 5)
    left = _renewed(c.get("/"))
    assert left and left > settings.SESSION_IDLE_MINUTES - 1


def test_renewed_at_most_once_a_minute(env):
    c = _signed_in()
    _age(c, settings.SESSION_IDLE_MINUTES - 0.5)  # renewed 30 seconds ago
    assert _renewed(c.get("/")) is None


def test_chat_polling_is_not_activity(env):
    c = _signed_in()
    _age(c, 5)
    assert _renewed(c.get("/chat/api/unread", headers={"accept": "application/json"})) is None


def test_signed_out_session_is_not_renewed(env):
    c = _signed_in()
    stolen = dict(c.cookies)
    c.post("/auth/logout", follow_redirects=False)  # revokes the refresh token
    other = TestClient(app)
    other.cookies.update(stolen)
    _age(other, 5)
    assert _renewed(other.get("/")) is None
