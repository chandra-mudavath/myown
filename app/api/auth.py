"""
Authentication API routes — thin controllers, business logic in auth_service.
"""

from __future__ import annotations

from fastapi import APIRouter, Cookie, Depends, Form, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.security import decode_token
from app.models.auth import AccountType
from app.schemas.auth import RegisterRequest
from app.services import auth_service

router = APIRouter(prefix="/auth", tags=["auth"])
templates = Jinja2Templates(directory="app/templates")

_COOKIE_OPTS = dict(httponly=True, samesite="lax", secure=False)  # secure=True in prod
_CTX = {"app_name": settings.APP_NAME}


def _ctx(request: Request, **extra) -> dict:
    return {"request": request, **_CTX, **extra}


# ── Pages ─────────────────────────────────────────────────────────────────────

@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse("auth/login.html", _ctx(request))


@router.get("/register", response_class=HTMLResponse)
def register_page(request: Request):
    return templates.TemplateResponse("auth/register.html", _ctx(request))


@router.get("/forgot-password", response_class=HTMLResponse)
def forgot_password_page(request: Request):
    return templates.TemplateResponse("auth/forgot-password.html", _ctx(request))


@router.get("/reset-password", response_class=HTMLResponse)
def reset_password_page(request: Request, token: str = ""):
    return templates.TemplateResponse("auth/reset-password.html", _ctx(request, token=token))


# @router.get("/verify-email", response_class=HTMLResponse)
# def verify_email_page(request: Request, token: str = ""):
#     """Handle email verification link click."""
#     if not token:
#         return templates.TemplateResponse("auth/verify-email.html", _ctx(request, error="Missing verification token."))
# 
#     db: Session = next(get_db())
#     try:
#         account = auth_service.verify_email_token(db, token)
#     finally:
#         db.close()
# 
#     if not account:
#         return templates.TemplateResponse("auth/verify-email.html", _ctx(request, error="Token is invalid or expired."))
#     return RedirectResponse(url="/auth/login?verified=1", status_code=status.HTTP_302_FOUND)


# ── Register ──────────────────────────────────────────────────────────────────

@router.post("/register", response_class=HTMLResponse)
def register(
    request: Request,
    first_name: str = Form(...),
    last_name: str = Form(None),
    email: str = Form(...),
    phone: str = Form(...),
    password: str = Form(...),
    password_confirmation: str = Form(...),
    db: Session = Depends(get_db),
):
    try:
        data = RegisterRequest(
            first_name=first_name,
            last_name=last_name,
            email=email,
            phone=phone,
            password=password,
            password_confirmation=password_confirmation,
        )
    except Exception as e:
        # Extract the first validation error message
        try:
            import json
            errors = json.loads(e.json())
            msg = errors[0]["msg"].replace("Value error, ", "")
        except Exception:
            msg = str(e)
        return templates.TemplateResponse("auth/register.html", _ctx(request, error=msg))

    if auth_service.get_account_by_email(db, data.email):
        return templates.TemplateResponse("auth/register.html", _ctx(request, error="An account with this email already exists."))

    _, _, raw_token = auth_service.register_client(db, data)

    # TODO: send email with verification link
    # email_service.send_verification(data.email, raw_token)
    # print(f"[DEV] Verify email -> http://127.0.0.1:8000/auth/verify-email?token={raw_token}")

    return RedirectResponse(url=request.url_for("login_page").include_query_params(registered=1), status_code=status.HTTP_303_SEE_OTHER)


# ── Login ─────────────────────────────────────────────────────────────────────

@router.post("/login", response_class=HTMLResponse)
def login(
    request: Request,
    response: Response,
    username: str = Form(...),   # OAuth2 convention; used as email
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    ip = request.client.host if request.client else None
    ua = request.headers.get("user-agent")

    account, failure = auth_service.authenticate(db, username, password, ip, ua)

    if not account:
        reasons = {
            "account_locked": "Your account is temporarily locked. Please try again later.",
            "account_inactive": "Your account has been deactivated.",
        }
        error = reasons.get(failure, "Invalid email or password.")
        return templates.TemplateResponse("auth/login.html", _ctx(request, error=error))

    # TODO: Re-enable email verification check when email sending is configured
    # if not account.is_verified:
    #     return templates.TemplateResponse(
    #         "auth/login.html",
    #         _ctx(request, error="Please verify your email before logging in.", show_resend=True, email=username),
    #     )

    access_token, raw_refresh = auth_service.issue_tokens(db, account)

    redirect_map = {
        AccountType.CLIENT: "client_dashboard",
        AccountType.STAFF:  "staff_dashboard",
        AccountType.ADMIN:  "admin_dashboard",
    }
    resp = RedirectResponse(url=request.url_for(redirect_map[account.account_type]), status_code=status.HTTP_303_SEE_OTHER)
    resp.set_cookie("access_token", access_token, max_age=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60, **_COOKIE_OPTS)
    resp.set_cookie("refresh_token", raw_refresh, max_age=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS * 86400, **_COOKIE_OPTS)
    return resp


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


# ── Forgot password ───────────────────────────────────────────────────────────

@router.post("/forgot-password", response_class=HTMLResponse)
def forgot_password(
    request: Request,
    email: str = Form(...),
    db: Session = Depends(get_db),
):
    account = auth_service.get_account_by_email(db, email)
    if account and account.is_active:
        raw_token = auth_service.create_password_reset_token(db, account)
        # TODO: send email
        print(f"[DEV] Reset password -> http://127.0.0.1:8000/auth/reset-password?token={raw_token}")

    msg = "If an account with that email exists, a reset link has been sent."
    return templates.TemplateResponse("auth/forgot-password.html", _ctx(request, msg=msg))


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
        return templates.TemplateResponse("auth/reset-password.html", {**ctx, "error": "Passwords do not match."})

    success = auth_service.reset_password(db, token, new_password)
    if not success:
        return templates.TemplateResponse("auth/reset-password.html", {**ctx, "error": "Invalid or expired reset token."})

    return RedirectResponse(url=request.url_for("login_page").include_query_params(reset=1), status_code=status.HTTP_303_SEE_OTHER)


# ── Resend verification ───────────────────────────────────────────────────────

# @router.post("/resend-verification", response_class=HTMLResponse)
# def resend_verification(
#     request: Request,
#     email: str = Form(...),
#     db: Session = Depends(get_db),
# ):
#     account = auth_service.get_account_by_email(db, email)
#     if account and not account.is_verified:
#         raw_token = auth_service.resend_verification(db, account)
#         print(f"[DEV] Verify email -> http://127.0.0.1:8000/auth/verify-email?token={raw_token}")
# 
#     msg = "If your account exists and is unverified, a new verification email has been sent."
#     return templates.TemplateResponse("auth/login.html", _ctx(request, msg=msg))
