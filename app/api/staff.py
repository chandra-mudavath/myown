from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import StaffAccount
from app.core.templates import templates
from app.models.staff import Staff

router = APIRouter(prefix="/staff", tags=["staff"])


@router.get("/dashboard", response_class=HTMLResponse)
def staff_dashboard(request: Request, account: StaffAccount, db: Session = Depends(get_db)):
    staff = db.query(Staff).filter(Staff.account_id == account.id).first()
    return templates.TemplateResponse(
        "staff/index.html", {"request": request, "account": account, "staff": staff}
    )
