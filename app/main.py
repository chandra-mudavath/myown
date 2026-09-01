from fastapi import FastAPI, HTTPException, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exception_handlers import http_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from app.api import auth, dashboard, profile, staff, admin
from app.api.client import client as client_routes
from app.core.config import settings
from app.core.database import Base, engine
from app.core.templates import templates
import app.models  # noqa: F401 — registers all models with Base.metadata

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


# Static files
app.mount("/static", StaticFiles(directory="app/static"), name="static")


@app.exception_handler(HTTPException)
async def http_error_handler(request: Request, exc: HTTPException):
    accepts_html = "text/html" in request.headers.get("accept", "")
    protected_page = request.url.path in {"/client/profile", "/client/dashboard", "/staff/dashboard", "/admin/dashboard"}
    if exc.status_code == 401 and accepts_html and protected_page:
        return templates.TemplateResponse(
            "errors/unauthorized.html",
            {"request": request, "message": "Please sign in to access this page."},
            status_code=401,
        )
    return await http_exception_handler(request, exc)

# Routers
app.include_router(auth.router)
app.include_router(dashboard.router)
app.include_router(profile.router)
app.include_router(client_routes.router)
app.include_router(staff.router)
app.include_router(admin.router)


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def root(request: Request):
    return templates.TemplateResponse("index.html", {"request": request, "app_name": settings.APP_NAME})