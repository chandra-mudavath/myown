from datetime import datetime, timezone

from fastapi import APIRouter, Cookie, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import ClientAccount
from app.core.templates import templates
from app.models.client import Client

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

TAX_YEAR_COOKIE = "tax_year"
_YEARS_BACK = 4  # selectable range: current year plus the 4 prior years


def _available_tax_years() -> list[int]:
    current_year = datetime.now(timezone.utc).year
    return [current_year - offset for offset in range(_YEARS_BACK + 1)]


@router.get("/select-year", response_class=HTMLResponse)
def select_year_page(request: Request, account: ClientAccount):
    return templates.TemplateResponse(
        "dashboard/select-year.html",
        {"request": request, "app_name": settings.APP_NAME, "years": _available_tax_years()},
    )


@router.post("/select-year")
def select_year_submit(request: Request, account: ClientAccount, tax_year: int = Form(...)):
    if tax_year not in _available_tax_years():
        return RedirectResponse(url=request.url_for("select_year_page"), status_code=status.HTTP_303_SEE_OTHER)

    resp = RedirectResponse(url=request.url_for("client_dashboard"), status_code=status.HTTP_303_SEE_OTHER)
    resp.set_cookie(TAX_YEAR_COOKIE, str(tax_year), max_age=180 * 86400, httponly=True, samesite="lax", secure=False)
    return resp


@router.get("", response_class=HTMLResponse)
def client_dashboard(
    request: Request,
    account: ClientAccount,
    db: Session = Depends(get_db),
    tax_year: str | None = Cookie(None),
):
    if not tax_year or not tax_year.isdigit() or int(tax_year) not in _available_tax_years():
        return RedirectResponse(url=request.url_for("select_year_page"), status_code=status.HTTP_303_SEE_OTHER)

    selected_year = int(tax_year)
    client = db.query(Client).filter(Client.account_id == account.id).first()
    display_name = client.first_name if client else account.email.split("@", 1)[0]
    initials = "".join(
        part[0].upper()
        for part in ((client.first_name, client.last_name) if client else (display_name, None))
        if part
    )[:2]
    updated_datetime = client.updated_at if client else account.updated_at
    updated_at = f"{updated_datetime:%B} {updated_datetime.day}, {updated_datetime:%Y}"
    dashboard = {
        "app_name": settings.APP_NAME,
        "display_name": display_name,
        "initials": initials,
        "tax_year": selected_year,
        "available_years": _available_tax_years(),
        "last_updated": updated_at,
        "filing": None,
        "summary": {
            "total_filings": 0,
            "in_progress": 0,
            "completed": 0,
            "action_required": 0,
            "payment_pending": 0,
        },
        "actions": [],
        "activities": [],
    }
    return templates.TemplateResponse(
        "dashboard/index.html",
        {"request": request, "account": account, "client": client, "dashboard": dashboard},
    )
