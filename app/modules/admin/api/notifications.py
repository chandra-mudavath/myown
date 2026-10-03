"""Admin notifications: client activity, stage moves and new accounts, newest first."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import AdminAccount
from app.core.templates import templates
from app.modules.admin.services.profile_service import current_admin
from app.modules.client.models.client import Client
from app.modules.staff.models.staff import Staff
from app.platform.models.auth import AccountType
from app.platform.services.activity_feed import aware, internal_activity

router = APIRouter(prefix="/admin", tags=["admin"])

KINDS = [("filing", "Filings"), ("upload", "Uploads"), ("reupload", "Re-uploads"), ("reply", "Replies"),
         ("stage", "Stage moves"), ("account", "New accounts")]


@router.get("/notifications", response_class=HTMLResponse, name="admin_notifications")
def admin_notifications(request: Request, account: AdminAccount, db: Session = Depends(get_db), kind: str = "all"):
    admin = current_admin(db, account)
    clients = {c.account_id: c.id for c in db.query(Client.account_id, Client.id).all()}
    staff = {s.account_id: s.id for s in db.query(Staff.account_id, Staff.id).all()}

    def account_link(a):
        if a.account_type == AccountType.CLIENT and a.id in clients:
            return f"/admin/clients/{clients[a.id]}"
        if a.account_type == AccountType.STAFF and a.id in staff:
            return f"/admin/staff/{staff[a.id]}"
        return None

    events = internal_activity(
        db,
        case_link=lambda f: f"/admin/cases/{f.id}",
        doc_link=lambda f, _doc: f"/admin/cases/{f.id}",
        account_link=account_link,
    )
    seen = aware(admin.notifications_seen_at) if admin else None
    for e in events:
        e["is_new"] = bool(e["at"]) and (seen is None or e["at"] > seen)
    counts = {k: sum(1 for e in events if e["kind"] == k) for k, _ in KINDS}
    new_count = sum(1 for e in events if e["is_new"])
    total = len(events)
    if kind == "new":
        events = [e for e in events if e["is_new"]]
    elif kind in counts:
        events = [e for e in events if e["kind"] == kind]
    else:
        kind = "all"

    response = templates.TemplateResponse("admin/notifications.html", {
        "request": request, "account": account, "admin": admin, "app_name": settings.APP_NAME,
        "active_nav": "notifications", "events": events, "kinds": KINDS, "kind": kind,
        "counts": counts, "new_count": new_count, "total": total,
    })
    # Viewing the full list marks everything as seen (filtered views don't, so nothing is missed).
    if admin and kind == "all":
        admin.notifications_seen_at = datetime.now(timezone.utc)
        db.commit()
    return response
