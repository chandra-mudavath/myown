"""Admin account controls on the client and staff pages."""
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import AdminAccount
from app.modules.admin.api.admin import render_client_detail, render_staff_detail
from app.modules.admin.services import accounts as svc
from app.modules.client.models.client import Client
from app.modules.staff.models.staff import Staff
from app.platform.models.auth import AuthAccount

router = APIRouter(prefix="/admin", tags=["admin"])


def _page_for(db: Session, account: AuthAccount) -> str:
    client = db.query(Client.id).filter(Client.account_id == account.id).scalar()
    if client:
        return f"/admin/clients/{client}"
    staff = db.query(Staff.id).filter(Staff.account_id == account.id).scalar()
    if staff:
        return f"/admin/staff/{staff}"
    raise HTTPException(status_code=404, detail="Account not found.")


def _back(url: str, err: str | None, saved: str) -> RedirectResponse:
    return RedirectResponse(f"{url}?{urlencode({'error': err} if err else {'saved': saved})}#account", status_code=303)


def _account_or_404(db: Session, account_id: str) -> AuthAccount:
    acc = db.get(AuthAccount, account_id)
    if not acc:
        raise HTTPException(status_code=404, detail="Account not found.")
    return acc


@router.post("/accounts/{account_id}/deactivate")
def account_deactivate(account_id: str, account: AdminAccount, db: Session = Depends(get_db)):
    target = _account_or_404(db, account_id)
    return _back(_page_for(db, target), svc.set_active(db, account.id, target, False), "Account deactivated. They can no longer sign in.")


@router.post("/accounts/{account_id}/activate")
def account_activate(account_id: str, account: AdminAccount, db: Session = Depends(get_db)):
    target = _account_or_404(db, account_id)
    return _back(_page_for(db, target), svc.set_active(db, account.id, target, True), "Account activated.")


@router.post("/accounts/{account_id}/unlock")
def account_unlock(account_id: str, account: AdminAccount, db: Session = Depends(get_db)):
    target = _account_or_404(db, account_id)
    return _back(_page_for(db, target), svc.unlock(db, account.id, target), "Account unlocked.")


@router.post("/accounts/{account_id}/reset-password")
def account_reset_password(account_id: str, request: Request, account: AdminAccount, db: Session = Depends(get_db)):
    target = _account_or_404(db, account_id)
    page = _page_for(db, target)
    password, err = svc.reset_password(db, account.id, target)
    if err:
        return _back(page, err, "")
    # Show the temporary password once, on the page itself: never in a URL, log or audit row.
    kind, person_id = page.rsplit("/", 2)[1:]
    render = render_client_detail if kind == "clients" else render_staff_detail
    response = render(request, person_id, account, db, temp_password=password)
    response.headers["Cache-Control"] = "no-store"
    return response


@router.post("/staff/{staff_id}/role")
def staff_role(staff_id: str, account: AdminAccount, db: Session = Depends(get_db), role_code: str = Form("")):
    staff = db.get(Staff, staff_id)
    if not staff:
        raise HTTPException(status_code=404, detail="Staff member not found.")
    return _back(f"/admin/staff/{staff_id}", svc.change_staff_role(db, account.id, staff, role_code), "Role updated.")


@router.post("/clients/{client_id}/edit")
def client_edit(client_id: str, account: AdminAccount, db: Session = Depends(get_db),
                first_name: str = Form(""), last_name: str = Form(""), phone: str = Form("")):
    client = db.get(Client, client_id)
    if not client:
        raise HTTPException(status_code=404, detail="Client not found.")
    return _back(f"/admin/clients/{client_id}", svc.update_client(db, account.id, client, first_name, last_name, phone), "Client details saved.")
