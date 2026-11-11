"""Admin: approve or reject clients' requests to change their sign-in email (client feedback, Nov 2026)."""
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import AdminAccount
from app.core.templates import templates
from app.modules.admin.services.profile_service import current_admin
from app.platform.models.auth import EmailChangeRequest
from app.platform.services import email_change

router = APIRouter(prefix="/admin", tags=["admin"])


def _back(err: str | None, saved: str) -> RedirectResponse:
    return RedirectResponse(f"/admin/email-requests?{urlencode({'error': err} if err else {'saved': saved})}", status_code=303)


def _request_or_404(db: Session, request_id: str) -> EmailChangeRequest:
    req = db.get(EmailChangeRequest, request_id)
    if not req:
        raise HTTPException(status_code=404, detail="Request not found.")
    return req


@router.get("/email-requests", response_class=HTMLResponse, name="admin_email_requests")
def email_requests(request: Request, account: AdminAccount, db: Session = Depends(get_db), saved: str = "", error: str = ""):
    return templates.TemplateResponse(request, "admin/email_requests.html", {
        "request": request, "account": account, "admin": current_admin(db, account), "app_name": settings.APP_NAME,
        "active_nav": "dashboard", "pending": email_change.pending(db), "recent": email_change.recent(db),
        "saved": saved, "error": error,
    })


@router.post("/email-requests/{request_id}/approve")
def email_request_approve(request_id: str, account: AdminAccount, db: Session = Depends(get_db)):
    req = _request_or_404(db, request_id)
    err = email_change.approve(db, account.id, req)
    return _back(err, f"Approved. {req.new_email} is now the client's sign-in email; the password is unchanged.")


@router.post("/email-requests/{request_id}/reject")
def email_request_reject(request_id: str, account: AdminAccount, db: Session = Depends(get_db), reason: str = Form("")):
    req = _request_or_404(db, request_id)
    return _back(email_change.reject(db, account.id, req, reason), "Rejected. The client can see the reason on Email & Mobile.")
