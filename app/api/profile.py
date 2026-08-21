from __future__ import annotations

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import ClientAccount
from app.models.client import Client
from app.schemas.client import (
    AddressInformationUpdate,
    ContactInformationUpdate,
    EmailChangeRequest,
    PersonalInformationUpdate,
)
from app.models.auth import AuthAccount
from app.services.client_profile import (
    get_client_for_account,
    serialize_profile,
    update_address,
    update_contact,
    update_personal,
)

router = APIRouter(prefix="/profile", tags=["client-profile"])
templates = Jinja2Templates(directory="app/templates")


def _client_or_404(db: Session, account_id: str) -> Client:
    client = get_client_for_account(db, account_id)
    if not client:
        from fastapi import HTTPException

        raise HTTPException(status.HTTP_404_NOT_FOUND, "Client profile not found")
    return client


@router.get("", response_class=HTMLResponse, name="client_profile")
def profile_page(request: Request, account: ClientAccount, db: Session = Depends(get_db)):
    client = _client_or_404(db, account.id)
    return templates.TemplateResponse(
        "profile/index.html",
        {"request": request, "account": account, "profile": serialize_profile(client, account)},
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

