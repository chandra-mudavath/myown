from datetime import datetime, timezone

from fastapi import APIRouter, Cookie, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import ClientAccount
from app.core.templates import templates
from app.models.client import Client
from app.models.filing_document import FilingDocument
from app.models.tax_filing import TaxFiling
from app.services.catalog import case_stages, default_tax_year, open_tax_years

router = APIRouter(prefix="/client", tags=["client-dashboard"])

TAX_YEAR_COOKIE = "tax_year"
def _available_tax_years(db: Session) -> list[int]:
    """Open years from the tax_years table, newest first (admins add a row to open a new year)."""
    return open_tax_years(db) or [datetime.now(timezone.utc).year]


def resolve_tax_year(db: Session, tax_year: str | None) -> int:
    """Return the cookie year when it is selectable, else the default year."""
    years = _available_tax_years(db)
    if tax_year and tax_year.isdigit() and int(tax_year) in years:
        return int(tax_year)
    default = default_tax_year(db)
    return default if default in years else years[0]


def client_shell_context(db: Session, account, tax_year: str | None) -> dict:
    """Values the shared client layout needs: identity, avatar and the year switcher."""
    client = db.query(Client).filter(Client.account_id == account.id).first()
    display_name = client.first_name if client and client.first_name else account.email.split("@", 1)[0]
    parts = (client.first_name, client.last_name) if client else (display_name, None)
    initials = "".join(part[0].upper() for part in parts if part)[:2]
    return {
        "app_name": settings.APP_NAME,
        "client": client,
        "account_email": account.email,
        "display_name": display_name,
        "initials": initials or "U",
        "available_years": _available_tax_years(db),
        "selected_year": resolve_tax_year(db, tax_year),
    }


def _merge_status(statuses: list[str]) -> str:
    """Collapse several section statuses into one stage status."""
    relevant = [s for s in statuses if s != "not_applicable"]
    if relevant and all(s == "complete" for s in relevant):
        return "complete"
    if any(s in ("complete", "in_progress") for s in relevant):
        return "in_progress"
    return "not_started"


def build_filing_stages(f: TaxFiling | None, section_statuses: list[dict], has_documents: bool) -> list[dict]:
    """Client-facing filing journey, in the order the client works through it."""
    by_num = {s["number"]: s["status"] for s in section_statuses}
    done = f is not None and f.status == "complete"
    later = "complete" if done else "not_started"
    return [
        {"label": "Personal Information", "url": "/client/personal-info",
         "status": _merge_status([by_num.get(n, "not_started") for n in ("01", "02", "03", "04")]) if f else "not_started"},
        {"label": "Tax Notes", "url": "/client/tax-notes",
         "status": _merge_status([by_num.get(n, "not_started") for n in ("05", "06", "08")]) if f else "not_started"},
        {"label": "Upload Documents", "url": "/client/documents",
         "status": "complete" if has_documents else "not_started"},
        {"label": "Estimated Tax Summary", "url": "/client/tax-summary", "status": later},
        {"label": "Preparation Charges Paid", "url": "/client/tax-summary#payment", "status": later},
        {"label": "Tax Return for Review", "url": "/client/review-documents", "status": later},
        {"label": "E-File Authorization", "url": "/client/efile-authorization", "status": later},
        {"label": "E-Filing / Paper Filing", "url": "/client/update-stage", "status": later},
    ]


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
def select_year_page(
    request: Request,
    account: ClientAccount,
    db: Session = Depends(get_db),
):
    client = db.query(Client).filter(Client.account_id == account.id).first()
    current_year = datetime.now(timezone.utc).year
    previous_filings: list[dict] = []

    if client:
        filings = (
            db.query(TaxFiling)
            .filter(TaxFiling.client_id == client.id, TaxFiling.tax_year < current_year)
            .order_by(TaxFiling.tax_year.desc(), TaxFiling.created_at.desc())
            .all()
        )
        seen_years: set[int] = set()
        for filing in filings:
            if filing.tax_year in seen_years:
                continue
            seen_years.add(filing.tax_year)
            previous_filings.append(
                {
                    "year": filing.tax_year,
                    "filing_type": filing.filing_type,
                    "status": _STATUS_LABEL.get(filing.status, filing.status),
                    "filed_date": f"{filing.created_at:%B} {filing.created_at.day}, {filing.created_at:%Y}",
                }
            )

    return templates.TemplateResponse(
        "client/dashboard/select-year.html",
        {
            "request": request,
            "app_name": settings.APP_NAME,
            "years": _available_tax_years(db),
            "previous_filings": previous_filings,
        },
    )


@router.post("/years")
def select_year_submit(
    request: Request,
    account: ClientAccount,
    tax_year: int = Form(...),
    next: str | None = Form(None),
    db: Session = Depends(get_db),
):
    client = db.query(Client).filter(Client.account_id == account.id).first()
    filed_years = set()
    if client:
        filed_years = {
            year
            for (year,) in db.query(TaxFiling.tax_year)
            .filter(TaxFiling.client_id == client.id)
            .distinct()
            .all()
        }

    if tax_year not in _available_tax_years(db) and tax_year not in filed_years:
        return RedirectResponse(url=request.url_for("select_year_page"), status_code=status.HTTP_303_SEE_OTHER)

    # Year switcher in the sidebar posts the current page so the client stays where they were.
    target = next if next and next.startswith("/client/") and not next.startswith("//") else str(request.url_for("client_dashboard"))
    resp = RedirectResponse(url=target, status_code=status.HTTP_303_SEE_OTHER)
    resp.set_cookie(TAX_YEAR_COOKIE, str(tax_year), max_age=180 * 86400, httponly=True, samesite="lax", secure=False)
    return resp


@router.get("/dashboard", response_class=HTMLResponse, name="client_dashboard")
def client_dashboard(
    request: Request,
    account: ClientAccount,
    db: Session = Depends(get_db),
    tax_year: str | None = Cookie(None),
):
    if not tax_year or not tax_year.isdigit() or int(tax_year) not in _available_tax_years(db):
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
            "case_id": filing_record.get_case_id(db),
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
    client_status = {s.code: s.client_status for s in case_stages(db)}
    reupload_requests = (
        db.query(FilingDocument)
        .filter(
            FilingDocument.filing_id.in_([f.id for f in all_filings]),
            FilingDocument.is_latest.is_(True),
            FilingDocument.status == "rejected_reupload_requested",
        )
        .count()
        if all_filings else 0
    )
    summary = {
        "total_filings": len(all_filings),
        "in_progress": sum(1 for f in all_filings if client_status.get(f.status) == "In Progress"),
        "completed": sum(1 for f in all_filings if client_status.get(f.status) == "Completed"),
        "action_required": reupload_requests,  # documents the tax team asked the client to re-upload
        "payment_pending": sum(1 for f in all_filings if f.status == "payment_pending"),
    }

    # Active filing exists if there is a filing that is not 'complete'
    has_active_filing = filing_record is not None and filing_record.status != "complete"

    document_count = (
        db.query(FilingDocument)
        .filter(FilingDocument.filing_id == filing_record.id, FilingDocument.is_latest == True)  # noqa: E712
        .count()
        if filing_record
        else 0
    )
    filing_stages = build_filing_stages(filing_record, section_statuses, document_count > 0)
    stages_done = sum(1 for s in filing_stages if s["status"] == "complete")

    dashboard = {
        "app_name": settings.APP_NAME,
        "display_name": display_name,
        "initials": initials,
        "tax_year": selected_year,
        "available_years": _available_tax_years(db),
        "last_updated": updated_at,
        "filing": filing_data,
        "section_statuses": section_statuses,
        "status_label": _STATUS_LABEL,
        "summary": summary,
        "actions": [],
        "activities": [],
        "has_active_filing": has_active_filing,
        "document_count": document_count,
        "filing_stages": filing_stages,
        "stages_done": stages_done,
        "stages_pct": int(stages_done / len(filing_stages) * 100),
    }
    return templates.TemplateResponse(
        "client/dashboard/index.html",
        {
            "request": request,
            "account": account,
            "dashboard": dashboard,
            **client_shell_context(db, account, tax_year),
        },
    )


