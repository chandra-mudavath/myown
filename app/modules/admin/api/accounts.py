"""Admin account controls on the client, staff and HR pages."""
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import AdminAccount
from app.modules.admin.api.admin import render_client_detail, render_staff_detail
from app.modules.admin.api.hr_accounts import render_hr_detail
from app.modules.admin.services import accounts as svc
from app.modules.client.models.client import Client
from app.modules.hr.models.hr import HR
from app.modules.hr.services import notify as hr_notify
from app.modules.staff.models.staff import Staff
from app.platform.models.auth import AccountType, AuthAccount

router = APIRouter(prefix="/admin", tags=["admin"])


def _page_for(db: Session, account: AuthAccount) -> str:
    client = db.query(Client.id).filter(Client.account_id == account.id).scalar()
    if client:
        return f"/admin/clients/{client}"
    hr = db.query(HR.id).filter(HR.account_id == account.id).scalar()
    if hr:
        return f"/admin/hr/{hr}"
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


def _tell_staff(request: Request, db: Session, target: AuthAccount, active: bool) -> None:
    """Staff and HR hear about it by email (when HR has that email turned on); clients don't."""
    person = (db.query(Staff).filter(Staff.account_id == target.id).first()
              or db.query(HR).filter(HR.account_id == target.id).first())
    if person and target.account_type != AccountType.CLIENT:
        hr_notify.account_active(db, hr_notify.base_url(request), person.first_name, target.email, active)


@router.post("/accounts/{account_id}/deactivate")
def account_deactivate(request: Request, account_id: str, account: AdminAccount, db: Session = Depends(get_db)):
    target = _account_or_404(db, account_id)
    err = svc.set_active(db, account.id, target, False)
    if not err:
        _tell_staff(request, db, target, False)
    return _back(_page_for(db, target), err, "Account deactivated. They can no longer sign in.")


@router.post("/accounts/{account_id}/activate")
def account_activate(request: Request, account_id: str, account: AdminAccount, db: Session = Depends(get_db)):
    target = _account_or_404(db, account_id)
    err = svc.set_active(db, account.id, target, True)
    if not err:
        _tell_staff(request, db, target, True)
    return _back(_page_for(db, target), err, "Account activated.")


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
    render = {"clients": render_client_detail, "hr": render_hr_detail}.get(kind, render_staff_detail)
    response = render(request, person_id, account, db, temp_password=password)
    response.headers["Cache-Control"] = "no-store"
    return response


@router.post("/staff/{staff_id}/role")
def staff_role(staff_id: str, account: AdminAccount, db: Session = Depends(get_db), role_code: str = Form(""),
               next: str = Form("")):
    staff = db.get(Staff, staff_id)
    if not staff:
        raise HTTPException(status_code=404, detail="Staff member not found.")
    who, account_id = f"{staff.first_name} {staff.last_name or ''}".strip(), staff.account_id
    err = svc.change_staff_role(db, account.id, staff, role_code)
    hr = db.query(HR.id).filter(HR.account_id == account_id).scalar() if not err else None
    if hr:  # moved to HR: they now have an HR account and an HR page
        return RedirectResponse(f"/admin/hr/{hr}?{urlencode({'saved': f'{who} is now HR. They sign in to the HR workspace.'})}",
                                status_code=303)
    if next.startswith("/admin/staff") and not next.startswith("//") and "#" not in next:
        # from the staff directory: back to the same list view, message naming the person
        params = {"error": err} if err else {"saved": f"{who}'s role is now {staff.role.value.title()}."}
        return RedirectResponse(f"{next}{'&' if '?' in next else '?'}{urlencode(params)}", status_code=303)
    return _back(f"/admin/staff/{staff_id}", err, "Role updated.")


@router.post("/clients/{client_id}/edit")
def client_edit(client_id: str, account: AdminAccount, db: Session = Depends(get_db),
                first_name: str = Form(""), last_name: str = Form(""), phone: str = Form("")):
    client = db.get(Client, client_id)
    if not client:
        raise HTTPException(status_code=404, detail="Client not found.")
    return _back(f"/admin/clients/{client_id}", svc.update_client(db, account.id, client, first_name, last_name, phone), "Client details saved.")
