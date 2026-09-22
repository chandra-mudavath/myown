from __future__ import annotations

from fastapi import APIRouter, Depends, File, UploadFile, Request, status
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import ClientAccount
from app.core.templates import templates
from app.core.config import settings
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
from app.services.storage_service import save_profile_picture

router = APIRouter(prefix="/client", tags=["client-profile"])


def _client_or_404(db: Session, account_id: str) -> Client:
    client = get_client_for_account(db, account_id)
    if not client:
        from fastapi import HTTPException

        raise HTTPException(status.HTTP_404_NOT_FOUND, "Client profile not found")
    return client


@router.get("/profile", response_class=HTMLResponse, name="client_profile")
@router.get("/personal-info", response_class=HTMLResponse, name="client_personal_info")
def profile_page(request: Request, account: ClientAccount, db: Session = Depends(get_db)):
    client = _client_or_404(db, account.id)
    return templates.TemplateResponse(
        "client/profile/index.html",
        {"request": request, "account": account, "profile": serialize_profile(client, account), "app_name": settings.APP_NAME, "active_nav": "personal_info"},
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

