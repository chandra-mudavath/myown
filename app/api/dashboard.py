from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import ClientAccount
from app.models.client import Client

router = APIRouter(prefix="/dashboard", tags=["dashboard"])
templates = Jinja2Templates(directory="app/templates")


@router.get("", response_class=HTMLResponse)
def client_dashboard(request: Request, account: ClientAccount, db: Session = Depends(get_db)):
    client = db.query(Client).filter(Client.account_id == account.id).first()
    display_name = client.first_name if client else account.email.split("@", 1)[0]
    initials = "".join(
        part[0].upper()
        for part in ((client.first_name, client.last_name) if client else (display_name, None))
        if part
    )[:2]
    updated_datetime = client.updated_at if client else account.updated_at
    updated_at = f"{updated_datetime:%B} {updated_datetime.day}, {updated_datetime:%Y}"
    dashboard = {
        "app_name": settings.APP_NAME,
        "display_name": display_name,
        "initials": initials,
        "tax_year": 2026,
        "last_updated": updated_at,
        "filing": None,
        "summary": {
            "total_filings": 0,
            "in_progress": 0,
            "completed": 0,
            "action_required": 0,
            "payment_pending": 0,
        },
        "actions": [],
        "activities": [],
    }
    return templates.TemplateResponse(
        "dashboard/index.html",
        {"request": request, "account": account, "client": client, "dashboard": dashboard},
    )
