"""Shared bits for the HR workspace routes: page context, redirects with a message, and the employee lookup."""
from __future__ import annotations

from urllib.parse import urlencode

from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.core.config import settings
from app.modules.hr.models.records import LeaveRequest
from app.modules.hr.services import hr_settings, people


def hr_ctx(request: Request, db: Session, account, **extra) -> dict:
    hr_settings.ensure_defaults(db)
    return {"request": request, "account": account, "hr": people.current_hr(db, account), "app_name": settings.APP_NAME,
            "pending_leaves": db.query(LeaveRequest.id).filter(LeaveRequest.status == "pending").count(), **extra}


def redirect(url: str, **params) -> RedirectResponse:
    """303 to url with ?saved= / ?error= added (before any #section)."""
    path, _, fragment = url.partition("#")
    query = urlencode({k: v for k, v in params.items() if v})
    if query:
        path += ("&" if "?" in path else "?") + query
    return RedirectResponse(path + (f"#{fragment}" if fragment else ""), status_code=303)


def employee_or_404(db: Session, staff_id: str):
    staff = people.employee(db, staff_id)
    if not staff:
        raise HTTPException(status_code=404, detail="Employee not found")
    return staff
