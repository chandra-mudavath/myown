from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import ClientAccount
from app.models.client import Client

router = APIRouter(prefix="/dashboard", tags=["dashboard"])
templates = Jinja2Templates(directory="app/templates")


@router.get("", response_class=HTMLResponse)
def client_dashboard(request: Request, account: ClientAccount, db: Session = Depends(get_db)):
    client = db.query(Client).filter(Client.account_id == account.id).first()
    return templates.TemplateResponse(
        "dashboard/index.html", {"request": request, "account": account, "client": client}
    )
