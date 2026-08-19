from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.api import auth, dashboard, staff, admin
from app.core.config import settings
from app.core.database import Base, engine
import app.models  # noqa: F401 — registers all models with Base.metadata

app = FastAPI(
    title=settings.APP_NAME,
    debug=settings.DEBUG,
    docs_url="/api/docs" if settings.DEBUG else None,
    redoc_url=None,
)


@app.on_event("startup")
def create_tables():
    Base.metadata.create_all(bind=engine)


# Static files
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")

# Routers
app.include_router(auth.router)
app.include_router(dashboard.router)
app.include_router(staff.router)
app.include_router(admin.router)


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def root(request: Request):
    return templates.TemplateResponse("index.html", {"request": request, "app_name": settings.APP_NAME})