from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.core.config import settings
from app.core.templates import templates

router = APIRouter(include_in_schema=False)


@router.get("/", response_class=HTMLResponse)
def root(request: Request):
    return templates.TemplateResponse(request, "index.html", {"request": request, "app_name": settings.APP_NAME})


@router.get("/about-us", response_class=HTMLResponse)
def about_us(request: Request):
    return templates.TemplateResponse(request, "about.html", {"request": request, "app_name": settings.APP_NAME})


@router.get("/services", response_class=HTMLResponse)
def services(request: Request):
    return templates.TemplateResponse(request, "services.html", {"request": request, "app_name": settings.APP_NAME})


@router.get("/refer-and-earn", response_class=HTMLResponse)
def refer_and_earn(request: Request):
    return templates.TemplateResponse(request, "refer.html", {"request": request, "app_name": settings.APP_NAME})


@router.get("/terms-and-conditions", response_class=HTMLResponse)
def terms_and_conditions(request: Request):
    return templates.TemplateResponse(request, "terms.html", {"request": request, "app_name": settings.APP_NAME})


@router.get("/disclaimer", response_class=HTMLResponse)
def disclaimer(request: Request):
    return templates.TemplateResponse(request, "disclaimer.html", {"request": request, "app_name": settings.APP_NAME})


# Branches on Contact Us. A blank field isn't shown; a branch with no address shows "address coming soon" and no map.
# US details are the ones in the footer (partials/landing_footer.html); keep the two in step.
BRANCHES = [
    {"key": "us", "flag": "us", "name": "United States", "tag": "Head office", "currency": "USD",
     "address": ["324 Aspen Way", "Lavon, Texas 75166", "USA"],
     "phone": "+1 (212) 555-0199", "email": "info@covrenttax.com", "hours": "Mon–Fri, 9 AM – 6 PM EST",
     "map_query": "324 Aspen Way, Lavon, TX 75166"},
    # TODO: India branch address, phone and hours (not on the site yet)
    {"key": "in", "flag": "in", "name": "India", "tag": "Branch office", "currency": "INR",
     "address": [], "phone": "", "email": "info@covrenttax.com", "hours": "", "map_query": ""},
]


@router.get("/contact-us", response_class=HTMLResponse)
def contact_us(request: Request):
    return templates.TemplateResponse(request, "contact.html", {"request": request, "app_name": settings.APP_NAME,
                                                                "branches": BRANCHES})
