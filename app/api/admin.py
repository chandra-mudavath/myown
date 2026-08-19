from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import AdminAccount
from app.models.admin import Admin

router = APIRouter(prefix="/admin", tags=["admin"])
templates = Jinja2Templates(directory="app/templates")


@router.get("/dashboard", response_class=HTMLResponse)
def admin_dashboard(request: Request, account: AdminAccount, db: Session = Depends(get_db)):
    admin = db.query(Admin).filter(Admin.account_id == account.id).first()
    return templates.TemplateResponse(
        "admin/index.html", {"request": request, "account": account, "admin": admin}
    )
