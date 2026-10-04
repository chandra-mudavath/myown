"""Admin: put staff on a case (or take them off). The case page shows the team and the form."""
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import AdminAccount
from app.platform.services import assignments

router = APIRouter(prefix="/admin/cases", tags=["admin"])


def _back(case_id: str, err: str | None, saved: str) -> RedirectResponse:
    query = urlencode({"error": err, "area": "team"} if err else {"saved": saved, "area": "team"})
    return RedirectResponse(f"/admin/cases/{case_id}?{query}#team", status_code=303)


@router.post("/{case_id}/assign")
def case_assign(case_id: str, account: AdminAccount, db: Session = Depends(get_db),
                staff_id: str = Form(""), role_code: str = Form("")):
    err = assignments.assign(db, account.id, case_id, staff_id, role_code or None)
    return _back(case_id, err, "Staff member assigned.")


@router.post("/{case_id}/assignments/{assignment_id}/end")
def case_unassign(case_id: str, assignment_id: str, account: AdminAccount, db: Session = Depends(get_db)):
    err = assignments.unassign(db, account.id, assignment_id, case_id)
    return _back(case_id, err, "Removed from the case.")
