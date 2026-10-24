import re
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.core import error_handlers
from app.core.assets import STATIC_MOUNTS, mount_name
from app.core.config import settings
from app.core.database import Base, engine
import app.db_models  # noqa: F401 — registers all models with Base.metadata
from app.modules.admin.api import referrals as admin_referrals, accounts as admin_accounts, admin, assignments as admin_assignments, case_actions as admin_case_actions, notifications as admin_notifications, profile as admin_profile, payments as admin_payments, reports as admin_reports, system_settings as admin_settings
from app.modules.client.api import client as client_routes, dashboard, profile
from app.modules.staff.api import staff
from app.platform.api import auth, chat
from app.public.api import pages

@asynccontextmanager
async def lifespan(_app: FastAPI):
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(
    lifespan=lifespan,
    title=settings.APP_NAME,
    debug=settings.DEBUG,
    docs_url="/api/docs" if settings.DEBUG else None,
    redoc_url=None,
)


# Error screens for pages, JSON with a code for fetch() calls, maintenance mode (see app/core/error_handlers.py)
error_handlers.register(app)


# Security headers on every response (added after the error middleware, so error screens get them too)
_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",            # browsers never guess a file's type
    "X-Frame-Options": "SAMEORIGIN",                # no framing by other sites (document previews are same-site)
    # Only our own site plus the few outside hosts the pages use (fonts, Tailwind, flag images, the footer map).
    # 'unsafe-inline' stays until the inline <script> blocks and onclick= handlers move into .js files.
    "Content-Security-Policy": "; ".join([
        "default-src 'self'",
        "script-src 'self' 'unsafe-inline' https://cdn.tailwindcss.com",
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
        "font-src 'self' data: https://fonts.gstatic.com",
        "img-src 'self' data: blob: https://flagcdn.com",
        "frame-src 'self' https://www.google.com https://maps.google.com",
        "connect-src 'self'",  # fetch() can't send data to another site
        "frame-ancestors 'self'", "base-uri 'self'", "object-src 'none'",
    ]),
    "Referrer-Policy": "strict-origin-when-cross-origin",  # reset-password tokens in a URL never leave the site
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}
if settings.is_production:
    _SECURITY_HEADERS["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    for name, value in _SECURITY_HEADERS.items():
        response.headers.setdefault(name, value)
    return response


# Production: answer only for our own host names, so a forged Host header can't end up in emailed links
if settings.is_production:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)


# Static files: each module serves its own static/ folder (see app/core/assets.py).
for module, prefix, directory in STATIC_MOUNTS:
    app.mount(prefix, StaticFiles(directory=directory), name=mount_name(module))
# Profile pictures are the only public files under storage/. Client documents also live there
# (storage/client_documents) and are served only by the signed-in download routes, so storage/
# is never mounted as a whole.
_SAFE_PART = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9_.-]{0,199}$")


@app.get("/storage/{owner_id}/avatar/{filename}", include_in_schema=False)
def avatar_file(owner_id: str, filename: str):
    path = Path("storage") / owner_id / "avatar" / filename
    if not (_SAFE_PART.match(owner_id) and _SAFE_PART.match(filename)) or ".." in filename             or owner_id == "client_documents" or not path.is_file():
        raise HTTPException(status_code=404)
    return FileResponse(path)


# Routers
app.include_router(auth.router)
app.include_router(dashboard.router)
app.include_router(profile.router)
app.include_router(client_routes.router)
app.include_router(staff.router)
app.include_router(admin.router)
app.include_router(admin_profile.router)
app.include_router(admin_notifications.router)
app.include_router(admin_settings.router)
app.include_router(admin_reports.router)
app.include_router(admin_payments.router)
app.include_router(admin_assignments.router)
app.include_router(admin_accounts.router)
app.include_router(admin_case_actions.router)
app.include_router(admin_referrals.router)
app.include_router(chat.router)
app.include_router(pages.router)
