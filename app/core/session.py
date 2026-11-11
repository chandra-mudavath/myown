"""Idle timeout (Nov 2026): signed in while working, signed out after SESSION_IDLE_MINUTES of doing nothing.

The access cookie lasts SESSION_IDLE_MINUTES. Each request the person makes renews it (at most once a minute),
so it only runs out when they stop. Before, it ran out 15 minutes after sign-in even mid-work: nothing ever
called /auth/refresh.

- Background polling (chat badge, inbox, open thread) isn't activity, or an open tab would never time out.
- It renews only while the sign-in's refresh token is still valid, so signing out, a password change or an
  admin's "sign out everywhere" also stops it. The refresh token's JWT_REFRESH_TOKEN_EXPIRE_DAYS caps a session.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import Request
from starlette.concurrency import run_in_threadpool

from app.core.config import settings
from app.core.database import get_db
from app.core.security import create_access_token, decode_token, hash_token
from app.platform.models.auth import AuthRefreshToken

COOKIE_OPTS = dict(httponly=True, samesite="lax", secure=settings.is_production)  # https-only cookies in production
_RENEW_AFTER_SECONDS = 60
_PASSIVE_GET_PREFIXES = ("/chat/api/",)  # polled every few seconds by chat.js and chat-badge.js
_SKIP_PREFIXES = ("/static/", "/auth/")  # files; sign-in, sign-out and codes set their own cookies


def set_access_cookie(response, email: str, account_type: str) -> None:
    response.set_cookie("access_token", create_access_token(email, account_type),
                        max_age=settings.SESSION_IDLE_MINUTES * 60, **COOKIE_OPTS)


def _session_alive(app, raw_refresh: str, email: str) -> bool:
    db_gen = app.dependency_overrides.get(get_db, get_db)()  # the app's get_db, or a test's override
    db = next(db_gen)
    try:
        row = (db.query(AuthRefreshToken)
               .filter(AuthRefreshToken.token_hash == hash_token(raw_refresh), AuthRefreshToken.revoked_at.is_(None),
                       AuthRefreshToken.expires_at > datetime.now(timezone.utc))
               .first())
        return bool(row and row.account and row.account.is_active and row.account.email == email)
    finally:
        db_gen.close()


async def renew_session(request: Request, call_next):
    response = await call_next(request)
    path = request.url.path
    token, raw_refresh = request.cookies.get("access_token"), request.cookies.get("refresh_token")
    if (not token or not raw_refresh or path.startswith(_SKIP_PREFIXES)
            or (request.method == "GET" and path.startswith(_PASSIVE_GET_PREFIXES))):
        return response
    if any(v.startswith(b"access_token=") for k, v in response.raw_headers if k == b"set-cookie"):
        return response  # the page itself signed someone in or out
    payload = decode_token(token)
    if not payload or payload.get("type") != "access":
        return response
    left = payload["exp"] - datetime.now(timezone.utc).timestamp()
    if left > settings.SESSION_IDLE_MINUTES * 60 - _RENEW_AFTER_SECONDS:
        return response  # renewed in the last minute
    if await run_in_threadpool(_session_alive, request.app, raw_refresh, payload["sub"]):
        set_access_cookie(response, payload["sub"], payload.get("account_type", ""))
    return response
