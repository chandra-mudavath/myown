from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import StaffAccount
from app.models.staff import Staff

router = APIRouter(prefix="/staff", tags=["staff"])
templates = Jinja2Templates(directory="app/templates")


@router.get("/dashboard", response_class=HTMLResponse)
def staff_dashboard(request: Request, account: StaffAccount, db: Session = Depends(get_db)):
    staff = db.query(Staff).filter(Staff.account_id == account.id).first()
    return templates.TemplateResponse(
        "staff/index.html", {"request": request, "account": account, "staff": staff}
    )
