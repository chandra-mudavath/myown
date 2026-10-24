"""Turn errors into the right response: an error screen for a page the browser opened, JSON for fetch().

register(app) installs:
- HTTPException handler (Starlette's class, so routing 404/405 are caught too)
- form validation handler (client profile API wording kept; pages get the "couldn't process" screen)
- middleware for maintenance mode and for unexpected exceptions (500, or 503 when the database is down)
- /_errors/{code} preview of every screen, only when DEBUG is on
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from urllib.parse import quote, urlparse

from fastapi import FastAPI, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from sqlalchemy.exc import OperationalError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.config import settings
from app.core.errors import BY_STATUS, SCREENS, AppError, _Blank
from app.core.security import decode_token
from app.core.templates import templates

log = logging.getLogger("app.errors")

DASHBOARDS = {"CLIENT": "/client/dashboard", "STAFF": "/staff/dashboard", "ADMIN": "/admin/dashboard"}
WHO = {"CLIENT": "a client", "STAFF": "a member of the tax team", "ADMIN": "an administrator"}

# "Back to …" for a record that wasn't found, by the section of the site it was opened from.
LISTS = [
    ("/admin/cases", "Back to cases", "/admin/cases"),
    ("/admin/payments", "Back to payments", "/admin/payments"),
    ("/admin/referrals", "Back to referrals", "/admin/referrals"),
    ("/admin/clients", "Back to clients", "/admin/clients"),
    ("/admin/staff", "Back to staff", "/admin/staff"),
    ("/staff/referrals", "Back to referrals", "/staff/referrals"),
    ("/staff/", "Back to dashboard", "/staff/dashboard"),
    ("/client/", "Back to dashboard", "/client/dashboard"),
]

# Sample values for the /_errors preview
_PREVIEW_PARAMS = {"AUTH-004": {"you": "a member of the tax team"}, "AUTH-005": {"you": "a client"},
                   "AUTH-006": {"you": "a client"}, "SYS-004": {"thing": "Case", "thing_lower": "case"},
                   "DOC-009": {"name": "W-2_2025.pdf"}}

_NOT_FOUND = re.compile(r"^(?P<thing>[A-Za-z][A-Za-z ]{0,40}?) not found\.?$")


def wants_html(request: Request) -> bool:
    """A page the browser opened (not a fetch() call or an API path)."""
    path = request.url.path
    if path.startswith("/api/") or "/api/" in path or path.endswith("/api"):
        return False
    return "text/html" in request.headers.get("accept", "")


def viewer_type(request: Request) -> str | None:
    """Account type of whoever is signed in, from the access cookie alone (no database)."""
    payload = decode_token(request.cookies.get("access_token") or "")
    return payload.get("account_type") if payload and payload.get("type") == "access" else None


def _return_path(request: Request) -> str | None:
    """Where to come back to after signing in: this page, or for a form post, the page the form was on."""
    if request.method == "GET":
        url = request.url
        return url.path + (f"?{url.query}" if url.query else "")
    referer = urlparse(request.headers.get("referer", ""))
    if referer.path and referer.netloc == request.url.netloc:
        return referer.path + (f"?{referer.query}" if referer.query else "")
    return None


def _action(key: str, request: Request, viewer: str | None) -> dict | None:
    home = DASHBOARDS.get(viewer, "/")
    back_onclick = "if (history.length > 1) { history.back(); return false; }"
    if key in ("sign_in", "sign_in_again"):
        nxt = _return_path(request)
        href = "/auth/login" + (f"?next={quote(nxt, safe='')}" if nxt else "")
        return {"label": "Sign in" if key == "sign_in" else "Sign in again", "href": href}
    if key == "home":
        return {"label": "Home", "href": "/"}
    if key == "dashboard":
        return {"label": "Go to my dashboard", "href": home} if viewer else {"label": "Home", "href": "/"}
    if key == "back":
        return {"label": "Go back", "href": home, "onclick": back_onclick}
    if key == "reload":
        if request.method == "GET":
            return {"label": "Try again", "href": str(request.url), "onclick": "location.reload(); return false;"}
        return {"label": "Try again", "href": _return_path(request) or home}
    if key == "support":
        return {"label": "Contact support", "href": f"mailto:{settings.EMAIL_REPLY_TO}"}
    if key == "change_password":
        return {"label": "Set my password", "href": "/auth/change-password"}
    if key == "switch_account":
        return {"label": "Sign in with another account", "form": "/auth/logout"}
    if key == "list":
        path = request.url.path
        for prefix, label, url in LISTS:
            if path.startswith(prefix):
                return {"label": label, "href": url}
        return _action("dashboard", request, viewer)
    return None


def render_error(request: Request, code: str, params: dict | None = None, *, status: int | None = None,
                 headers: dict | None = None) -> Response:
    """The error screen for this code."""
    screen = SCREENS[code]
    viewer = viewer_type(request)
    values = _Blank(app_name=settings.APP_NAME, max_mb=settings.MAX_UPLOAD_SIZE_MB, you=WHO.get(viewer, "someone else"))
    values.update({k: v for k, v in (params or {}).items() if v})
    actions, seen = [], set()
    for key in screen.actions:
        a = _action(key, request, viewer)
        if a and (a["label"], a.get("href")) not in seen:
            seen.add((a["label"], a.get("href")))
            actions.append(a)
    try:
        resp = templates.TemplateResponse(request, "errors/error.html", {
            "request": request, "app_name": settings.APP_NAME, "screen": screen,
            "title": screen.title.format_map(values), "problem": screen.problem.format_map(values),
            "fix": screen.fix.format_map(values), "actions": actions, "support_email": settings.EMAIL_REPLY_TO,
            "year": datetime.now().year,
        }, status_code=status or screen.status, headers=headers)
    except Exception:  # the error page itself must never fail
        log.exception("Error page %s failed to render", code)
        resp = PlainTextResponse(f"{screen.title.format_map(values)}\n{screen.problem.format_map(values)}",
                                 status_code=status or screen.status, headers=headers)
    resp.headers["Cache-Control"] = "no-store"
    if code in ("AUTH-002", "AUTH-003"):
        resp.delete_cookie("access_token")
        resp.delete_cookie("refresh_token")
    return resp


def _json(code: str, detail, status: int, headers: dict | None = None) -> JSONResponse:
    return JSONResponse({"detail": detail, "code": code}, status_code=status, headers=headers)


def code_for(exc: StarletteHTTPException) -> tuple[str, dict]:
    """The screen for an HTTPException: its own code, a "… not found" record, or one picked by status."""
    if isinstance(exc, AppError):
        return exc.code, exc.params
    if exc.status_code == 404 and isinstance(exc.detail, str):
        m = _NOT_FOUND.match(exc.detail.strip())
        if m:
            thing = m.group("thing").strip()
            return "SYS-004", {"thing": thing[0].upper() + thing[1:], "thing_lower": thing.lower()}
    code = BY_STATUS.get(exc.status_code) or ("SYS-002" if exc.status_code >= 500 else "SYS-011")
    return code, {}


async def http_error(request: Request, exc: StarletteHTTPException):
    code, params = code_for(exc)
    if exc.status_code >= 500:
        log.error("HTTP %s on %s %s: %s", exc.status_code, request.method, request.url.path, exc.detail)
    if wants_html(request):
        return render_error(request, code, params, status=exc.status_code, headers=getattr(exc, "headers", None))
    return _json(code, exc.detail, exc.status_code, getattr(exc, "headers", None))


_PROFILE_FIELDS = {
    "first_name": "Please enter your first name.",
    "last_name": "Please enter a valid last name.",
    "date_of_birth": "Please enter a valid date of birth.",
    "address_line_1": "Please enter your address.",
    "city": "Please enter your city.",
    "state_province": "Please enter your state or province.",
    "postal_code": "Please enter a valid postal code.",
    "country": "Please enter your country.",
    "phone": "Please enter a valid phone number.",
    "email": "Please enter a valid email address.",
}


async def validation_error(request: Request, exc: RequestValidationError):
    if request.url.path.startswith("/client/profile/api"):
        field = exc.errors()[0].get("loc", ["profile"])[-1]
        return JSONResponse(status_code=422, content={"detail": _PROFILE_FIELDS.get(field, "Please check the information you entered.")})
    if wants_html(request):
        return render_error(request, "SYS-011")
    return await request_validation_exception_handler(request, exc)


def _maintenance_exempt(request: Request) -> bool:
    path = request.url.path
    if path.startswith(("/static", "/storage", "/auth", "/_errors")):
        return True  # assets, and sign-in so an admin can still get in
    return viewer_type(request) == "ADMIN"


async def error_middleware(request: Request, call_next):
    if settings.MAINTENANCE_MODE and not _maintenance_exempt(request):
        headers = {"Retry-After": "600"}
        if wants_html(request):
            return render_error(request, "SYS-007", headers=headers)
        return _json("SYS-007", "The service is temporarily unavailable.", 503, headers)
    try:
        return await call_next(request)
    except Exception as exc:
        log.exception("Unhandled error on %s %s", request.method, request.url.path)
        code = "SYS-007" if isinstance(exc, OperationalError) else "SYS-002"
        if wants_html(request):
            return render_error(request, code)
        return _json(code, SCREENS[code].problem.format_map(_Blank(app_name=settings.APP_NAME)), SCREENS[code].status)


def preview(request: Request, code: str):
    """DEBUG only: show one error screen, e.g. /_errors/AUTH-006."""
    code = code.upper()
    if code not in SCREENS:
        raise AppError("SYS-001")
    return render_error(request, code, _PREVIEW_PARAMS.get(code))


def register(app: FastAPI) -> None:
    app.add_exception_handler(StarletteHTTPException, http_error)
    app.add_exception_handler(RequestValidationError, validation_error)
    app.middleware("http")(error_middleware)
    if settings.DEBUG:
        app.add_api_route("/_errors/{code}", preview, methods=["GET"], include_in_schema=False)
