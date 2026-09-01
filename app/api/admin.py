from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import AdminAccount
from app.core.templates import templates
from app.models.admin import Admin

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/dashboard", response_class=HTMLResponse, name="admin_dashboard")
def admin_dashboard(request: Request, account: AdminAccount, db: Session = Depends(get_db)):
    admin = db.query(Admin).filter(Admin.account_id == account.id).first()
    return templates.TemplateResponse(
        "admin/index.html", {"request": request, "account": account, "admin": admin}
    )
