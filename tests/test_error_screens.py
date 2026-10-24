"""Error screens: pages the browser opens get a screen, fetch() calls get JSON with a code."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.core import error_handlers
from app.core.config import settings
from app.core.deps import get_current_account
from app.core.errors import SCREENS, AppError
from app.core.files import send_file
from app.main import app
from app.platform.api.auth import _safe_next
from app.platform.models.auth import AccountType

HTML = {"accept": "text/html,application/xhtml+xml"}
JSON = {"accept": "application/json"}


@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=False)


def test_unknown_page_shows_not_found_screen(client):
    r = client.get("/no-such-page", headers=HTML)
    assert r.status_code == 404
    assert "find that page" in r.text and "e-card" in r.text
    assert r.headers["cache-control"] == "no-store"


def test_unknown_api_path_gets_json_with_code(client):
    r = client.get("/no-such-page", headers=JSON)
    assert r.status_code == 404 and r.json()["code"] == "SYS-001"


def test_signed_out_page_offers_sign_in_that_returns_here(client):
    r = client.get("/client/dashboard?x=1", headers=HTML)
    assert r.status_code == 401
    assert "Please sign in" in r.text
    assert "/auth/login?next=%2Fclient%2Fdashboard%3Fx%3D1" in r.text


def test_expired_session_screen_clears_cookies(client):
    client.cookies.set("refresh_token", "old")
    r = client.get("/staff/dashboard", headers=HTML)
    assert r.status_code == 401 and "session has expired" in r.text
    assert 'refresh_token=""' in r.headers.get("set-cookie", "")


def test_fetch_call_keeps_old_detail_and_adds_code(client):
    r = client.get("/chat/api/inbox", headers=HTML)  # an API path never gets a screen
    assert r.status_code == 401
    assert r.json() == {"detail": "Not authenticated", "code": "AUTH-001"}


def test_wrong_portal_names_both_sides(client):
    app.dependency_overrides[get_current_account] = lambda: SimpleNamespace(account_type=AccountType.CLIENT, must_change_password=False)
    try:
        r = client.get("/admin/dashboard", headers=HTML)
    finally:
        app.dependency_overrides.clear()
    assert r.status_code == 403
    assert "for administrators" in r.text and "signed in as a client" in r.text
    assert "Sign in with another account" in r.text


def test_get_on_post_only_url_shows_method_screen(client):
    r = client.get("/auth/logout", headers=HTML)
    assert r.status_code == 405 and "opened directly" in r.text


def test_unexpected_error_shows_friendly_screen_and_json(client):
    def boom():
        raise RuntimeError("kaboom")

    def db_down():
        raise OperationalError("SELECT 1", {}, Exception("gone"))

    app.add_api_route("/_test/boom", boom)
    app.add_api_route("/_test/db-down", db_down)
    r = client.get("/_test/boom", headers=HTML)
    assert r.status_code == 500 and "Something went wrong" in r.text and "kaboom" not in r.text
    assert client.get("/_test/boom", headers=JSON).json()["code"] == "SYS-002"
    r = client.get("/_test/db-down", headers=HTML)
    assert r.status_code == 503 and "be right back" in r.text


def test_maintenance_mode_blocks_pages_but_not_sign_in(client, monkeypatch):
    monkeypatch.setattr(settings, "MAINTENANCE_MODE", True)
    r = client.get("/", headers=HTML)
    assert r.status_code == 503 and r.headers["retry-after"] == "600"
    assert client.get("/auth/login", headers=HTML).status_code == 200


def test_record_not_found_uses_the_record_name():
    code, params = error_handlers.code_for(HTTPException(404, "Tax filing not found."))
    assert code == "SYS-004" and params == {"thing": "Tax filing", "thing_lower": "tax filing"}
    assert error_handlers.code_for(HTTPException(404, "Not Found"))[0] == "SYS-001"


def test_missing_stored_file_raises_file_screen(tmp_path):
    with pytest.raises(AppError) as e:
        send_file(str(tmp_path / "gone.pdf"), "W-2_2025.pdf")
    assert e.value.code == "DOC-009" and e.value.params["name"] == "W-2_2025.pdf"


def test_login_remembers_only_safe_return_paths(client):
    r = client.get("/auth/login?next=/client/documents", headers=HTML)
    assert 'login_next="/client/documents"' in r.headers.get("set-cookie", "")
    for bad in ("//evil.com/x", "https://evil.com", "/\\evil.com", "javascript:alert(1)"):
        assert _safe_next(bad) is None, bad
        r = client.get("/auth/login", params={"next": bad}, headers=HTML)
        assert "login_next" not in r.headers.get("set-cookie", "")


@pytest.mark.skipif(not settings.DEBUG, reason="preview route exists only with DEBUG on")
@pytest.mark.parametrize("code", sorted(SCREENS))
def test_every_screen_renders_in_preview(client, code):
    r = client.get(f"/_errors/{code}", headers=HTML)
    assert r.status_code == SCREENS[code].status
    assert "e-card" in r.text and "{" not in r.text.split("<h1", 1)[1].split("</h1>", 1)[0]
