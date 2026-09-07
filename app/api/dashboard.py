from datetime import datetime, timezone

from fastapi import APIRouter, Cookie, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import ClientAccount
from app.core.templates import templates
from app.models.client import Client
from app.models.tax_filing import TaxFiling

router = APIRouter(prefix="/client", tags=["client-dashboard"])

TAX_YEAR_COOKIE = "tax_year"
_YEARS_BACK = 4  # selectable range: current year plus the 4 prior years


def _available_tax_years() -> list[int]:
    current_year = datetime.now(timezone.utc).year
    return [current_year - offset for offset in range(_YEARS_BACK + 1)]


def _section_status(value) -> str:
    """Return a UI status label based on whether a value is populated."""
    if value is None:
        return "not_started"
    if isinstance(value, list):
        return "complete" if value else "not_started"
    if isinstance(value, str):
        return "complete" if value.strip() else "not_started"
    return "complete"


def _build_section_statuses(f: TaxFiling) -> list[dict]:
    """Derive per-section completion status from a TaxFiling record."""
    def _all(*fields) -> str:
        return "complete" if all(v for v in fields) else ("in_progress" if any(v for v in fields) else "not_started")

    def _list_status(lst) -> str:
        return "complete" if lst else "not_started"

    # Spouse applicability — only relevant for married statuses
    married = f.filing_status in ("married_jointly", "married_separately")
    spouse_status = (
        "complete" if married and f.spouse_first_name and f.spouse_last_name
        else "not_applicable" if not married
        else "in_progress" if married
        else "not_started"
    )

    return [
        {"number": "01", "label": "Basic Information",        "status": _all(f.first_name, f.last_name, f.email, f.filing_type, f.tax_year)},
        {"number": "02", "label": "Personal & Filing Details","status": _all(f.date_of_birth, f.us_tax_residency_status, f.filing_status, f.address_line_1, f.city, f.country)},
        {"number": "03", "label": "Spouse Information",       "status": spouse_status},
        {"number": "04", "label": "Dependents",               "status": _list_status(f.dependents)},
        {"number": "05", "label": "Income Information",       "status": _list_status(f.income_categories)},
        {"number": "06", "label": "Deductions & Credits",     "status": _list_status(f.deductions_credits)},
        {"number": "07", "label": "Previous Tax Information", "status": _all(f.has_previous_return_copy, f.received_irs_notice, f.unresolved_tax_issues, f.is_amended_return)},
        {"number": "08", "label": "Special Situations",       "status": _list_status(f.special_situations)},
        {"number": "09", "label": "Documents",                "status": "pending"},
    ]


_STATUS_LABEL = {
    "complete":     "Complete",
    "in_progress":  "In Progress",
    "not_started":  "Not Started",
    "not_applicable": "Not Applicable",
    "pending":      "Pending",
    "pending_review": "Pending Review",
}


@router.get("/years", response_class=HTMLResponse, name="select_year_page")
def select_year_page(request: Request, account: ClientAccount):
    return templates.TemplateResponse(
        "client/dashboard/select-year.html",
        {"request": request, "app_name": settings.APP_NAME, "years": _available_tax_years()},
    )


@router.post("/years")
def select_year_submit(request: Request, account: ClientAccount, tax_year: int = Form(...)):
    if tax_year not in _available_tax_years():
        return RedirectResponse(url=request.url_for("select_year_page"), status_code=status.HTTP_303_SEE_OTHER)

    resp = RedirectResponse(url=request.url_for("client_dashboard"), status_code=status.HTTP_303_SEE_OTHER)
    resp.set_cookie(TAX_YEAR_COOKIE, str(tax_year), max_age=180 * 86400, httponly=True, samesite="lax", secure=False)
    return resp


@router.get("/dashboard", response_class=HTMLResponse, name="client_dashboard")
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

    # --- Query the most recent TaxFiling for this client + year ---
    filing_record: TaxFiling | None = None
    section_statuses: list[dict] = []
    filing_data: dict | None = None

    if client:
        filing_record = (
            db.query(TaxFiling)
            .filter(TaxFiling.client_id == client.id, TaxFiling.tax_year == selected_year)
            .order_by(TaxFiling.created_at.desc())
            .first()
        )

    if filing_record:
        section_statuses = _build_section_statuses(filing_record)
        completed_sections = sum(1 for s in section_statuses if s["status"] == "complete")
        total_applicable = sum(1 for s in section_statuses if s["status"] != "not_applicable")
        filing_created = f"{filing_record.created_at:%B} {filing_record.created_at.day}, {filing_record.created_at:%Y}"
        filing_updated = f"{filing_record.updated_at:%B} {filing_record.updated_at.day}, {filing_record.updated_at:%Y}"
        filing_data = {
            "id": filing_record.id,
            "case_id": filing_record.id[:8].upper(),
            "tax_year": filing_record.tax_year,
            "filing_type": filing_record.filing_type,
            "status": _STATUS_LABEL.get(filing_record.status, filing_record.status),
            "created_date": filing_created,
            "last_updated": filing_updated,
            "completed_sections": completed_sections,
            "total_applicable_sections": total_applicable,
            "progress_pct": int((completed_sections / total_applicable) * 100) if total_applicable else 0,
            "section_statuses": section_statuses,
        }

    # Summary counts (across all years for this client)
    all_filings = db.query(TaxFiling).filter(TaxFiling.client_id == client.id).all() if client else []
    summary = {
        "total_filings": len(all_filings),
        "in_progress": sum(1 for f in all_filings if f.status == "in_progress"),
        "completed": sum(1 for f in all_filings if f.status == "complete"),
        "action_required": 0,
        "payment_pending": 0,
    }

    # Active filing exists if there is a filing that is not 'complete'
    has_active_filing = filing_record is not None and filing_record.status != "complete"

    dashboard = {
        "app_name": settings.APP_NAME,
        "display_name": display_name,
        "initials": initials,
        "tax_year": selected_year,
        "available_years": _available_tax_years(),
        "last_updated": updated_at,
        "filing": filing_data,
        "section_statuses": section_statuses,
        "status_label": _STATUS_LABEL,
        "summary": summary,
        "actions": [],
        "activities": [],
        "has_active_filing": has_active_filing,
    }
    return templates.TemplateResponse(
        "client/dashboard/index.html",
        {"request": request, "account": account, "client": client, "dashboard": dashboard},
    )


