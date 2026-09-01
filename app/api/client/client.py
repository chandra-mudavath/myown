from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import ClientAccount
from app.core.templates import templates
from app.models.client import Client
from app.models.tax_filing import TaxFiling
from app.schemas.tax_filing import TaxFilingCreate

router = APIRouter(prefix="/client", tags=["client"])


@router.get("/filings/new", response_class=HTMLResponse, name="new_filing_page")
async def new_filing_page(
    request: Request,
    account: ClientAccount,
    db: Session = Depends(get_db),
):
    return templates.TemplateResponse(
        "client/new_filing.html",
        {
            "request": request,
            "app_name": settings.APP_NAME,
            "available_years": [2024, 2025, 2026],
        },
    )


@router.post("/filings/new", name="submit_new_filing")
async def submit_new_filing(
    request: Request,
    account: ClientAccount,
    db: Session = Depends(get_db),
):
    # Look up the Client record to get the client_id
    client = db.query(Client).filter(Client.account_id == account.id).first()
    if not client:
        return RedirectResponse(
            url=request.url_for("client_dashboard"),
            status_code=status.HTTP_303_SEE_OTHER,
        )

    form_data = await request.form()

    # Parse checkbox lists (multiple values with the same key)
    income_categories = list(form_data.getlist("income_categories"))
    deductions_credits = list(form_data.getlist("deductions_credits"))
    special_situations = list(form_data.getlist("special_situations"))

    # Build a flat dict of scalar fields, converting empty strings to None
    data: dict = {}
    skip_keys = {"income_categories", "deductions_credits", "special_situations"}
    for key, value in form_data.items():
        if key not in skip_keys:
            data[key] = value if value != "" else None

    # Inject parsed list fields
    data["income_categories"] = income_categories
    data["deductions_credits"] = deductions_credits
    data["special_situations"] = special_situations
    data["dependents"] = []  # Dependents are not yet dynamic in the JS; defaults to empty

    # Cast numeric fields before Pydantic validation
    if data.get("tax_year"):
        data["tax_year"] = int(data["tax_year"])
    if data.get("previous_tax_year_filed"):
        data["previous_tax_year_filed"] = int(data["previous_tax_year_filed"])

    # Validate and parse via Pydantic
    parsed = TaxFilingCreate(**data)

    # Persist to DB
    tax_filing = TaxFiling(client_id=client.id, **parsed.model_dump())
    db.add(tax_filing)
    db.commit()
    db.refresh(tax_filing)

    return RedirectResponse(
        url=request.url_for("client_dashboard"),
        status_code=status.HTTP_303_SEE_OTHER,
    )
