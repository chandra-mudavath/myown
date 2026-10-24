from __future__ import annotations

from urllib.parse import urlencode

from fastapi import APIRouter, Cookie, Depends, File, UploadFile, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.modules.client.api.dashboard import client_shell_context, resolve_tax_year
from app.core.database import get_db
from app.core.deps import ClientAccount
from app.core.templates import templates
from app.core.config import settings
from app.modules.client.models.client import Client
from app.modules.client.schemas.client import (
    AddressInformationUpdate,
    ContactInformationUpdate,
    EmailChangeRequest,
    PersonalInformationUpdate,
)
from app.platform.models.auth import AuthAccount
from app.modules.client.services.client_profile import (
    get_client_for_account,
    serialize_profile,
    update_address,
    update_contact,
    update_personal,
)
from app.platform.services import intake
from app.platform.services.storage_service import save_profile_picture

router = APIRouter(prefix="/client", tags=["client-profile"])


def _client_or_404(db: Session, account_id: str) -> Client:
    client = get_client_for_account(db, account_id)
    if not client:
        from fastapi import HTTPException

        raise HTTPException(status.HTTP_404_NOT_FOUND, "Client profile not found")
    return client


@router.get("/profile", response_class=HTMLResponse, name="client_profile")
@router.get("/personal-info", response_class=HTMLResponse, name="client_personal_info")
def profile_page(
    request: Request,
    account: ClientAccount,
    db: Session = Depends(get_db),
    tax_year: str | None = Cookie(None),
):
    client = _client_or_404(db, account.id)
    filing = intake.filing_for(db, client, resolve_tax_year(db, tax_year))
    residency = intake.section(db, filing, intake.RESIDENCY)
    dependents = intake.section(db, filing, intake.DEPENDENTS)
    return templates.TemplateResponse(
        request, "client/profile/index.html",
        {
            "request": request,
            "account": account,
            "profile": serialize_profile(client, account),
            **client_shell_context(db, account, tax_year),
            "active_nav": "personal_info",
            # Visa & residency and Dependents are saved per tax year (were UI only until Oct 2026)
            "residency": (residency.data if residency else {}) or {}, "residency_row": residency,
            "dependents": ((dependents.data or {}).get("items") if dependents else None) or [],
            "visas": intake.VISAS, "nationalities": intake.NATIONALITIES, "relationships": intake.RELATIONSHIPS,
            "form_saved": request.query_params.get("saved", ""), "form_error": request.query_params.get("error", ""),
            "form_for": request.query_params.get("for", ""),
        },
    )



@router.get("/api")
def get_profile(account: ClientAccount, db: Session = Depends(get_db)):
    client = _client_or_404(db, account.id)
    return serialize_profile(client, account)


@router.put("/api/personal")
def save_personal(data: PersonalInformationUpdate, account: ClientAccount, db: Session = Depends(get_db)):
    client = _client_or_404(db, account.id)
    update_personal(client, data)
    db.commit()
    db.refresh(client)
    return {"message": "Your personal information has been updated successfully.", "profile": serialize_profile(client, account)}


@router.put("/api/address")
def save_address(data: AddressInformationUpdate, account: ClientAccount, db: Session = Depends(get_db)):
    client = _client_or_404(db, account.id)
    update_address(client, data)
    db.commit()
    db.refresh(client)
    return {"message": "Your address has been updated successfully.", "profile": serialize_profile(client, account)}


@router.put("/api/contact")
def save_contact(data: ContactInformationUpdate, account: ClientAccount, db: Session = Depends(get_db)):
    client = _client_or_404(db, account.id)
    update_contact(client, data)
    db.commit()
    db.refresh(client)
    return {"message": "Your phone number has been updated successfully.", "profile": serialize_profile(client, account)}


@router.post("/api/email-request")
def email_change_request(data: EmailChangeRequest, account: ClientAccount, db: Session = Depends(get_db)):
    if data.email.lower() == account.email.lower():
        return {"message": "This is already your current email address."}
    existing = db.query(AuthAccount).filter(AuthAccount.email == str(data.email).lower(), AuthAccount.id != account.id).first()
    if existing:
        from fastapi import HTTPException

        raise HTTPException(status.HTTP_409_CONFLICT, "This email address is already associated with another account.")
    return {"message": "Email verification is currently unavailable. Your current email address was not changed."}


@router.post("/api/avatar")
def upload_avatar(
    account: ClientAccount,
    file: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    client = _client_or_404(db, account.id)
    avatar_url = save_profile_picture(file, client.id, "client")
    client.profile_picture = avatar_url
    db.commit()
    return {"message": "Profile picture updated successfully!", "avatar_url": avatar_url}


# ── Visa & residency, Dependents (saved per tax year) ────────────────────────

def _back(err: str | None, saved: str, panel: str, anchor: str | None = None) -> RedirectResponse:
    """Back to Personal Info; the message shows in `panel`, the page opens at `anchor` (default: the same panel)."""
    query = urlencode({"error": err, "for": panel} if err else {"saved": saved, "for": panel})
    anchor = anchor or panel
    return RedirectResponse(f"/client/profile?{query}#{anchor}", status_code=status.HTTP_303_SEE_OTHER)


def _year_case(db: Session, account, tax_year: str | None):
    client = _client_or_404(db, account.id)
    return intake.ensure_filing(db, client, db.get(AuthAccount, account.id), resolve_tax_year(db, tax_year))


@router.post("/profile/residency", name="client_save_residency")
async def save_residency(request: Request, account: ClientAccount, db: Session = Depends(get_db), tax_year: str | None = Cookie(None)):
    data, err = intake.residency_from_form(await request.form())
    if err:
        return _back(err, "", "return-type")
    intake.save_section(db, _year_case(db, account, tax_year), intake.RESIDENCY, data, account.id, submitted=True)
    return _back(None, "Residency details saved.", "dependents")  # the page moves on to Dependents


@router.post("/profile/dependents", name="client_save_dependent")
async def save_dependent(request: Request, account: ClientAccount, db: Session = Depends(get_db), tax_year: str | None = Cookie(None)):
    """Add a dependent, or remove one (action=remove, index=n)."""
    form = await request.form()
    client = _client_or_404(db, account.id)
    filing = intake.filing_for(db, client, resolve_tax_year(db, tax_year))
    row = intake.section(db, filing, intake.DEPENDENTS)
    items = list(((row.data or {}).get("items") if row else None) or [])
    if form.get("action") == "remove":
        index = str(form.get("index") or "")
        if not (index.isdigit() and int(index) < len(items)):
            return _back("That dependent was already removed.", "", "dependents")
        items.pop(int(index))
        saved = "Dependent removed."
    else:
        dep, err = intake.dependent_from_form(form)
        if not err and len(items) >= intake.MAX_DEPENDENTS:
            err = f"You can add up to {intake.MAX_DEPENDENTS} dependents. Tell your tax team about the rest."
        if err:
            return _back(err, "", "dependents")
        items.append(dep)
        saved = "Dependent added."
    intake.save_section(db, filing or _year_case(db, account, tax_year), intake.DEPENDENTS, {"items": items}, account.id, submitted=True)
    return _back(None, saved, "dependents")

