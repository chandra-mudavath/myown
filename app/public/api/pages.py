from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.core.config import settings
from app.core.templates import templates

router = APIRouter(include_in_schema=False)


@router.get("/", response_class=HTMLResponse)
def root(request: Request):
    return templates.TemplateResponse("index.html", {"request": request, "app_name": settings.APP_NAME})


@router.get("/about-us", response_class=HTMLResponse)
def about_us(request: Request):
    return templates.TemplateResponse("about.html", {"request": request, "app_name": settings.APP_NAME})


@router.get("/services", response_class=HTMLResponse)
def services(request: Request):
    return templates.TemplateResponse("services.html", {"request": request, "app_name": settings.APP_NAME})


@router.get("/refer-and-earn", response_class=HTMLResponse)
def refer_and_earn(request: Request):
    return templates.TemplateResponse("refer.html", {"request": request, "app_name": settings.APP_NAME})


@router.get("/terms-and-conditions", response_class=HTMLResponse)
def terms_and_conditions(request: Request):
    return templates.TemplateResponse("terms.html", {"request": request, "app_name": settings.APP_NAME})
