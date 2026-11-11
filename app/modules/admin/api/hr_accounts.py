"""Admin page for one HR account: who they are, and the same account controls as staff and clients."""
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import AdminAccount
from app.core.templates import templates
from app.modules.admin.models.admin import Admin
from app.modules.admin.services.accounts import lock_state
from app.modules.hr.models.hr import HR

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/hr/{hr_id}", name="admin_hr_detail")
def admin_hr_detail(request: Request, hr_id: str, account: AdminAccount, db: Session = Depends(get_db),
                    saved: str = "", error: str = ""):
    return render_hr_detail(request, hr_id, account, db, saved=saved, error=error)


def render_hr_detail(request: Request, hr_id: str, account, db: Session, saved: str = "", error: str = "",
                     temp_password: str | None = None):
    """temp_password is only passed by the reset-password action, never from the URL."""
    hr = db.get(HR, hr_id)
    if not hr:
        raise HTTPException(status_code=404, detail="HR member not found")
    return templates.TemplateResponse(request, "admin/hr/detail.html", {
        "request": request, "account": account, "admin": db.query(Admin).filter(Admin.account_id == account.id).first(),
        "hr_member": hr, "target": hr.account, "locked_until": lock_state(hr.account) if hr.account else None,
        "saved": saved, "error": error, "temp_password": temp_password, "active_nav": "staff", "app_name": settings.APP_NAME,
    })
