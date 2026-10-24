"""
Authentication API routes — thin controllers, business logic in auth_service.
"""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Cookie, Depends, Form, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
import phonenumbers
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import CurrentAccount
from app.core.errors import AppError
from app.core.security import decode_token
from app.core.templates import templates
from app.platform.models.auth import AccountType, AuthAccount
from app.platform.schemas.auth import RegisterRequest, validate_password_strength
from app.modules.client.models.client import Client
from app.platform.services import admin_signup, auth_service, email_service, login_otp, referrals
from app.platform.tasks import email_tasks

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/countries")
def countries() -> list[dict[str, str | int]]:
    """Return the supported country regions and their international dial codes."""
    result = []
    for region in sorted(phonenumbers.SUPPORTED_REGIONS):
        dial_code = phonenumbers.country_code_for_region(region)
        if dial_code:
            result.append({"region": region, "dial_code": dial_code})
    return result

_COOKIE_OPTS = dict(httponly=True, samesite="lax", secure=settings.is_production)  # https-only cookies in production
_CTX = {"app_name": settings.APP_NAME}


def _ctx(request: Request, **extra) -> dict:
    return {"request": request, **_CTX, **extra}


# ── Pages ─────────────────────────────────────────────────────────────────────

@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str = ""):
    resp = templates.TemplateResponse(request, "auth/login.html", _ctx(request))
    # Opened from a "Please sign in" screen: remember the page so sign-in (and the emailed code step) returns there
    if _safe_next(next):
        resp.set_cookie(_NEXT_COOKIE, _safe_next(next), max_age=900, path="/auth", **_COOKIE_OPTS)
    return resp


_NEXT_COOKIE = "login_next"
# A return path must be in the signed-in person's own portal, or it would just show "You don't have access"
_PORTAL = {AccountType.CLIENT: "/client/", AccountType.STAFF: "/staff/", AccountType.ADMIN: "/admin/"}


def _safe_next(path: str | None) -> str | None:
    """A same-site path to come back to after sign-in, or None (blocks //host, backslashes and schemes)."""
    if not path or not path.startswith("/") or path.startswith("//") or "\\" in path or ":" in path.split("?", 1)[0]:
        return None
    return path[:500]


@router.get("/register", response_class=HTMLResponse)
def register_page(request: Request, ref: str = ""):
    return templates.TemplateResponse(request, "auth/register.html", _ctx(request, ref=ref[:16]))


@router.get("/staff/register", response_class=HTMLResponse, name="staff_register_page")
def staff_register_page(request: Request):
    if not settings.STAFF_SELF_SIGNUP:
        return templates.TemplateResponse(request, "auth/login.html", _ctx(request, error=_STAFF_BY_ADMIN))
    return templates.TemplateResponse(request, "auth/register.html", _ctx(request, signup_role="staff"))


_STAFF_BY_ADMIN = "Company accounts (@covrenttax.com) are created by an administrator. Ask your admin to add you."


# Open admin sign-up page: switched off (client feedback, Oct 2026). Admins sign up only from the
# separate link an admin generates in Settings, see admin_signup_page below.
# @router.get("/admin/register", response_class=HTMLResponse, name="admin_register_page")
def admin_register_page(request: Request):
    return templates.TemplateResponse(request, "auth/register.html", _ctx(request, signup_role="admin"))


@router.get("/admin/signup/{key}", response_class=HTMLResponse, name="admin_signup_page")
def admin_signup_page(key: str, request: Request, db: Session = Depends(get_db)):
    """The admin-only sign-up page. Only the current link from Admin Settings works."""
    if not admin_signup.is_valid(db, key):
        raise AppError("AUTH-015", "This sign-up link is not valid.")
    return templates.TemplateResponse(request, "auth/register.html", _ctx(request, signup_role="admin", admin_key=key))


@router.get("/forgot-password", response_class=HTMLResponse)
def forgot_password_page(request: Request):
    return templates.TemplateResponse(request, "auth/forgot-password.html", _ctx(request))


@router.get("/reset-password", response_class=HTMLResponse)
def reset_password_page(request: Request, token: str = ""):
    return templates.TemplateResponse(request, "auth/reset-password.html", _ctx(request, token=token))


# ── Register ──────────────────────────────────────────────────────────────────

@router.post("/register", response_class=HTMLResponse)
def register(
    request: Request,
    background_tasks: BackgroundTasks,
    first_name: str = Form(...),
    last_name: str = Form(None),
    email: str = Form(...),
    country_code: str = Form(""),
    phone_local: str = Form(""),
    password: str = Form(...),
    password_confirmation: str = Form(...),
    signup_role: str = Form("client"),
    admin_key: str = Form(""),
    ref: str = Form(""),
    db: Session = Depends(get_db),
):
    if not (country_code and phone_local):  # the form requires it; clients.phone can't be empty
        return templates.TemplateResponse(request, "auth/register.html", _ctx(
            request, error="Enter your mobile number.", signup_role=signup_role, admin_key=admin_key, ref=ref))
    try:
        phone = f"{country_code}{phone_local}"
        data = RegisterRequest(
            first_name=first_name,
            last_name=last_name,
            email=email,
            phone=phone,
            password=password,
            password_confirmation=password_confirmation,
        )
    except Exception as e:
        try:
            import json
            errors = json.loads(e.json())
            msg = errors[0]["msg"].replace("Value error, ", "")
        except Exception:
            msg = str(e)
        return templates.TemplateResponse(request, "auth/register.html", _ctx(request, error=msg, signup_role=signup_role, admin_key=admin_key, ref=ref))

    ip = request.client.host if request.client else None
    if auth_service.registrations_blocked(db, ip):
        return templates.TemplateResponse(request, "auth/register.html", _ctx(
            request, error="Too many accounts were created from your network. Please try again in an hour.",
            signup_role=signup_role, admin_key=admin_key, ref=ref), status_code=status.HTTP_429_TOO_MANY_REQUESTS)

    if auth_service.get_account_by_email(db, data.email):
        return templates.TemplateResponse(request, "auth/register.html", _ctx(request, error="An account with this email already exists.", signup_role=signup_role, admin_key=admin_key, ref=ref))

    # Admins sign up only through the admin link (client feedback, Oct 2026)
    from_admin_link = signup_role == "admin" and admin_signup.is_valid(db, admin_key)
    if signup_role == "admin" and not from_admin_link:
        raise AppError("AUTH-015", "This sign-up link is not valid.")
    if not from_admin_link and auth_service.is_admin_email(data.email):
        return templates.TemplateResponse(request, "auth/register.html", _ctx(
            request, error="Admin accounts can only be created from the admin sign-up link.", signup_role=signup_role))

    # Staff and HR accounts are made by an admin, unless STAFF_SELF_SIGNUP is on
    if not from_admin_link and auth_service.is_internal_email(data.email) and not settings.STAFF_SELF_SIGNUP:
        return templates.TemplateResponse(request, "auth/register.html", _ctx(request, error=_STAFF_BY_ADMIN, signup_role=signup_role, ref=ref))

    # The admin link is for the internal team only: company email addresses (@covrenttax.com)
    if from_admin_link and not auth_service.is_internal_email(data.email):
        return templates.TemplateResponse(request, "auth/register.html", _ctx(
            request, error="Use your company email address (@covrenttax.com) to create an admin account.",
            signup_role=signup_role, admin_key=admin_key))

    if from_admin_link:
        account, _ = auth_service.register_admin(db, data)
    else:
        account, profile = auth_service.register_by_email_domain(db, data)
        # Signed up with a client's referral link: link them so the tax team can see it
        if ref and isinstance(profile, Client):
            referrals.record_signup(db, profile, data.email, ref)

    auth_service.record_registration(db, account, ip)

    # Clients are verified immediately — send them straight to login
    if account.is_verified:
        return RedirectResponse(
            url=request.url_for("login_page").include_query_params(registered=1),
            status_code=status.HTTP_303_SEE_OTHER,
        )

    # Staff / Admin: confirm the company address with the code emailed to it
    return _start_code(request, background_tasks, db, account, "signup")


# ── Login ─────────────────────────────────────────────────────────────────────

def _login(
    request: Request,
    response: Response,
    background_tasks: BackgroundTasks,
    username: str = Form(...),   # OAuth2 convention; used as email
    password: str = Form(...),
    db: Session = Depends(get_db),
    expected_type: AccountType | None = None,
    login_role: str = "client",
):
    ip = request.client.host if request.client else None
    ua = request.headers.get("user-agent")

    if auth_service.ip_blocked(db, ip):
        return templates.TemplateResponse(request, "auth/login.html", _ctx(
            request, login_role=login_role,
            error=f"Too many sign-in attempts. Please wait {auth_service.LOCKOUT_MINUTES} minutes and try again."),
            status_code=status.HTTP_429_TOO_MANY_REQUESTS)

    account, failure = auth_service.authenticate(db, username, password, ip, ua)

    if not account:
        reasons = {
            "account_locked": f"Too many wrong passwords. Please wait {auth_service.LOCKOUT_MINUTES} minutes, "
                              "or use Forgot password to reset it and sign in right away.",
            "account_inactive": "Your account has been deactivated.",
        }
        error = reasons.get(failure, "Invalid email or password.")
        return templates.TemplateResponse(request, "auth/login.html", _ctx(request, error=error, login_role=login_role))

    if expected_type is not None and account.account_type != expected_type:
        role_name = expected_type.value.title()
        return templates.TemplateResponse(
            request, "auth/login.html",
            _ctx(request, login_role=login_role, error=f"This login is for {role_name.lower()} accounts only."),
            status_code=status.HTTP_403_FORBIDDEN,
        )

    # Staff / Admin (HR included): the password is step one; step two is the code emailed to them
    if account.account_type != AccountType.CLIENT:
        return _start_code(request, background_tasks, db, account, "login")
    return _signed_in(request, db, account)


def _signed_in(request: Request, db: Session, account: AuthAccount) -> RedirectResponse:
    """Issue the session cookies and send the person back to the page they were trying to open, or their dashboard."""
    access_token, raw_refresh = auth_service.issue_tokens(db, account)

    redirect_map = {
        AccountType.CLIENT: "select_year_page",
        AccountType.STAFF:  "staff_dashboard",
        AccountType.ADMIN:  "admin_dashboard",
    }
    target = _safe_next(request.cookies.get(_NEXT_COOKIE))
    if not (target and target.startswith(_PORTAL[account.account_type])):
        target = str(request.url_for(redirect_map[account.account_type]))
    if account.must_change_password:  # a temporary password from an admin: choose their own first
        target = str(request.url_for("change_password_page"))
    resp = RedirectResponse(url=target, status_code=status.HTTP_303_SEE_OTHER)
    resp.set_cookie("access_token", access_token, max_age=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60, **_COOKIE_OPTS)
    resp.set_cookie("refresh_token", raw_refresh, max_age=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS * 86400, **_COOKIE_OPTS)
    resp.delete_cookie(_OTP_COOKIE, path="/auth")
    resp.delete_cookie(_NEXT_COOKIE, path="/auth")
    return resp


# ── Emailed sign-in / sign-up code (staff and admin) ─────────────────────────

_OTP_COOKIE = "otp_challenge"
_CODE_GONE = "That code has expired or had too many wrong tries. Sign in again to get a new one."


def _start_code(request: Request, background_tasks: BackgroundTasks, db: Session, account: AuthAccount,
                purpose: str) -> Response:
    """Email a 6-digit code (after the response) and send the browser to the code page."""
    started = login_otp.start(db, account, purpose)
    if not started:
        return templates.TemplateResponse(request, "auth/login.html", _ctx(
            request, error="Too many sign-in codes were requested for this account. Please try again in an hour."),
            status_code=status.HTTP_429_TOO_MANY_REQUESTS)
    challenge, code = started
    background_tasks.add_task(email_tasks.enqueue, email_tasks.send_login_code, account.email, code, purpose, login_otp.OTP_EXPIRE_MINUTES)
    resp = RedirectResponse(url=request.url_for("verify_code_page"), status_code=status.HTTP_303_SEE_OTHER)
    resp.set_cookie(_OTP_COOKIE, challenge, max_age=3600, path="/auth", **_COOKIE_OPTS)
    return resp


def _code_page(request: Request, db: Session, otp, **extra):
    account = db.get(AuthAccount, otp.account_id)
    return templates.TemplateResponse(request, "auth/verify-code.html", _ctx(
        request, purpose=otp.purpose, masked_email=login_otp.mask_email(account.email),
        minutes=login_otp.OTP_EXPIRE_MINUTES, **extra))


def _code_gone(request: Request):
    resp = templates.TemplateResponse(request, "auth/login.html", _ctx(request, error=_CODE_GONE))
    resp.delete_cookie(_OTP_COOKIE, path="/auth")
    return resp


@router.get("/verify-code", response_class=HTMLResponse, name="verify_code_page")
def verify_code_page(request: Request, otp_challenge: str | None = Cookie(None), db: Session = Depends(get_db)):
    otp = login_otp.pending(db, otp_challenge)
    if not otp:
        return RedirectResponse(url=request.url_for("login_page"), status_code=status.HTTP_303_SEE_OTHER)
    return _code_page(request, db, otp)


@router.post("/verify-code", response_class=HTMLResponse)
def verify_code(
    request: Request,
    code: str = Form(...),
    otp_challenge: str | None = Cookie(None),
    db: Session = Depends(get_db),
):
    otp, error = login_otp.verify(db, otp_challenge, code)
    if otp:
        return _signed_in(request, db, db.get(AuthAccount, otp.account_id))
    if error == "wrong":
        left = login_otp.OTP_MAX_ATTEMPTS - login_otp.pending(db, otp_challenge).attempts
        return _code_page(request, db, login_otp.pending(db, otp_challenge),
                          error=f"That code isn't right. {left} {'try' if left == 1 else 'tries'} left.")
    return _code_gone(request)


@router.post("/verify-code/resend", response_class=HTMLResponse)
def resend_code(
    request: Request,
    background_tasks: BackgroundTasks,
    otp_challenge: str | None = Cookie(None),
    db: Session = Depends(get_db),
):
    otp, code, error = login_otp.resend(db, otp_challenge)
    if otp:
        account = db.get(AuthAccount, otp.account_id)
        background_tasks.add_task(email_tasks.enqueue, email_tasks.send_login_code, account.email, code, otp.purpose,
                                  login_otp.OTP_EXPIRE_MINUTES)
        return _code_page(request, db, otp, msg="We've sent a new code. Earlier codes no longer work.")
    if error == "wait":
        return _code_page(request, db, login_otp.pending(db, otp_challenge),
                          error="Please wait a minute before asking for another code.")
    return _code_gone(request)


@router.post("/login", response_class=HTMLResponse, name="login")
def login(
    request: Request,
    response: Response,
    background_tasks: BackgroundTasks,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    return _login(request, response, background_tasks, username, password, db)


# ── Logout ────────────────────────────────────────────────────────────────────

@router.post("/logout")
def logout(
    request: Request,
    refresh_token: str | None = Cookie(None),
    db: Session = Depends(get_db),
):
    if refresh_token:
        auth_service.revoke_refresh_token(db, refresh_token)
    resp = RedirectResponse(url=request.url_for("login_page"), status_code=status.HTTP_303_SEE_OTHER)
    resp.delete_cookie("access_token")
    resp.delete_cookie("refresh_token")
    return resp


# ── Refresh ───────────────────────────────────────────────────────────────────

@router.post("/refresh")
def refresh(
    refresh_token: str | None = Cookie(None),
    db: Session = Depends(get_db),
):
    if not refresh_token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "No refresh token")
    result = auth_service.rotate_refresh_token(db, refresh_token)
    if not result:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Refresh token invalid or expired")
    access_token, new_raw_refresh = result
    resp = Response()
    resp.set_cookie("access_token", access_token, max_age=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60, **_COOKIE_OPTS)
    resp.set_cookie("refresh_token", new_raw_refresh, max_age=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS * 86400, **_COOKIE_OPTS)
    return {"ok": True}


# ── Change a temporary password (accounts an admin created or reset) ──────────

_HOME = {AccountType.CLIENT: "select_year_page", AccountType.STAFF: "staff_dashboard", AccountType.ADMIN: "admin_dashboard"}


@router.get("/change-password", response_class=HTMLResponse, name="change_password_page")
def change_password_page(request: Request, account: CurrentAccount):
    if not account.must_change_password:
        return RedirectResponse(url=request.url_for(_HOME[account.account_type]), status_code=status.HTTP_303_SEE_OTHER)
    return templates.TemplateResponse(request, "auth/change-password.html", _ctx(request))


@router.post("/change-password", response_class=HTMLResponse)
def change_password(
    request: Request,
    account: CurrentAccount,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    refresh_token: str | None = Cookie(None),
    db: Session = Depends(get_db),
):
    err = auth_service.change_password(db, account, current_password, new_password, confirm_password,
                                       keep_refresh_token=refresh_token, ip=request.client.host if request.client else None)
    if err:
        return templates.TemplateResponse(request, "auth/change-password.html", _ctx(request, error=err))
    return RedirectResponse(url=request.url_for(_HOME[account.account_type]), status_code=status.HTTP_303_SEE_OTHER)


# ── Forgot password ───────────────────────────────────────────────────────────

@router.post("/forgot-password")
def forgot_password(
    request: Request,
    background_tasks: BackgroundTasks,
    email: str = Form(...),
    db: Session = Depends(get_db),
):
    """Email a reset link. The reply is the same whether or not the account exists, so the form
    can't be used to find out who has an account."""
    account = auth_service.get_account_by_email(db, email)
    if account and account.is_active and auth_service.can_send_password_reset(db, account):
        raw_token = auth_service.create_password_reset_token(db, account)
        link = email_service.public_url(request, f"/auth/reset-password?token={raw_token}")
        background_tasks.add_task(email_tasks.enqueue, email_tasks.send_password_reset, account.email, link,
                                  auth_service.PASSWORD_RESET_EXPIRE_MINUTES)
    msg = (
        "If an account exists for that email, we've sent it a link to reset the password. "
        f"The link expires in {auth_service.PASSWORD_RESET_EXPIRE_MINUTES} minutes."
    )
    return templates.TemplateResponse(request, "auth/forgot-password.html", _ctx(request, msg=msg))


# ── Reset password ────────────────────────────────────────────────────────────

@router.post("/reset-password", response_class=HTMLResponse)
def reset_password(
    request: Request,
    token: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    db: Session = Depends(get_db),
):
    ctx = _ctx(request, token=token)

    if new_password != confirm_password:
        return templates.TemplateResponse(request, "auth/reset-password.html", {**ctx, "error": "Passwords do not match."})
    try:
        validate_password_strength(new_password)  # same rules as sign-up
    except ValueError as e:
        return templates.TemplateResponse(request, "auth/reset-password.html", {**ctx, "error": str(e)})

    success = auth_service.reset_password(db, token, new_password)
    if not success:
        return templates.TemplateResponse(request, "auth/reset-password.html", {**ctx, "error": "Invalid or expired reset token."})

    msg = "Password updated successfully! Redirecting to login..."
    login_url = str(request.url_for("login_page").include_query_params(reset=1))
    return templates.TemplateResponse(
        request, "auth/reset-password.html",
        {**ctx, "msg": msg, "redirect_url": login_url}
    )
