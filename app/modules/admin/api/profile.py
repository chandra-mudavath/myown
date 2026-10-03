"""Admin "My Profile" page: name and password. The photo uses the existing /admin/profile/avatar upload."""
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import AdminAccount
from app.core.templates import templates
from app.modules.admin.services.profile_service import change_password, current_admin, update_name

router = APIRouter(prefix="/admin", tags=["admin"])


def _back(**params) -> RedirectResponse:
    query = urlencode({k: v for k, v in params.items() if v})
    return RedirectResponse(f"/admin/profile{'?' + query if query else ''}", status_code=303)


@router.get("/profile", response_class=HTMLResponse, name="admin_profile")
def admin_profile(request: Request, account: AdminAccount, db: Session = Depends(get_db),
                  saved: str = "", error: str = ""):
    return templates.TemplateResponse("admin/profile.html", {
        "request": request, "account": account, "admin": current_admin(db, account), "app_name": settings.APP_NAME,
        "active_nav": "profile", "saved": saved, "error": error,
    })


@router.post("/profile")
def admin_profile_save(account: AdminAccount, db: Session = Depends(get_db),
                       first_name: str = Form(""), last_name: str = Form("")):
    admin = current_admin(db, account)
    if not admin:
        return _back(error="Admin profile not found.")
    err = update_name(db, admin, first_name, last_name)
    return _back(error=err) if err else _back(saved="name")


@router.post("/profile/password")
def admin_password_save(request: Request, account: AdminAccount, db: Session = Depends(get_db),
                        current_password: str = Form(""), new_password: str = Form(""), confirm_password: str = Form("")):
    err = change_password(db, account, current_password, new_password, confirm_password,
                          keep_refresh_token=request.cookies.get("refresh_token"),
                          ip=request.client.host if request.client else None)
    return _back(error=err) if err else _back(saved="password")
