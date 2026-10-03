import os

from fastapi import FastAPI, HTTPException, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exception_handlers import http_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.core.config import settings
from app.core.database import Base, engine
from app.core.templates import templates
import app.db_models  # noqa: F401 — registers all models with Base.metadata
from app.modules.admin.api import admin
from app.modules.client.api import client as client_routes, dashboard, profile
from app.modules.staff.api import staff
from app.platform.api import auth, chat
from app.public.api import pages

app = FastAPI(
    title=settings.APP_NAME,
    debug=settings.DEBUG,
    docs_url="/api/docs" if settings.DEBUG else None,
    redoc_url=None,
)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    if request.url.path.startswith("/client/profile/api"):
        field = exc.errors()[0].get("loc", ["profile"])[-1]
        messages = {
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
        return JSONResponse(status_code=422, content={"detail": messages.get(field, "Please check the information you entered.")})
    return await request_validation_exception_handler(request, exc)


@app.on_event("startup")
def create_tables():
    Base.metadata.create_all(bind=engine)


# Static files: each module serves its own static/ folder under /static/<module>/.
# Module mounts must come before the shared /static mount, which would otherwise swallow their paths.
for module, directory in [
    ("public", "app/public/static"),
    ("client", "app/modules/client/static"),
    ("staff", "app/modules/staff/static"),
    ("admin", "app/modules/admin/static"),
    ("hr", "app/modules/hr/static"),
]:
    app.mount(f"/static/{module}", StaticFiles(directory=directory), name=f"{module}_static")
app.mount("/static", StaticFiles(directory="app/platform/static"), name="static")
# storage/ is git-ignored upload data, so create it on a fresh checkout before mounting
os.makedirs("storage", exist_ok=True)
app.mount("/storage", StaticFiles(directory="storage"), name="storage")


@app.exception_handler(HTTPException)
async def http_error_handler(request: Request, exc: HTTPException):
    accepts_html = "text/html" in request.headers.get("accept", "")
    is_browser_route = not request.url.path.startswith("/api/") and not request.url.path.endswith("/api")
    if exc.status_code in (401, 403) and accepts_html and is_browser_route:
        login_url = "/auth/login"
        if request.url.path.startswith("/staff"):
            login_url = "/auth/login?role=staff"
        elif request.url.path.startswith("/admin"):
            login_url = "/auth/login?role=admin"

        return templates.TemplateResponse(
            "errors/unauthorized.html",
            {
                "request": request,
                "message": exc.detail or "Access restricted. Please authenticate with appropriate privileges.",
                "login_url": login_url,
                "status_code": exc.status_code
            },
            status_code=exc.status_code,
        )
    return await http_exception_handler(request, exc)

# Routers
app.include_router(auth.router)
app.include_router(dashboard.router)
app.include_router(profile.router)
app.include_router(client_routes.router)
app.include_router(staff.router)
app.include_router(admin.router)
app.include_router(chat.router)
app.include_router(pages.router)
