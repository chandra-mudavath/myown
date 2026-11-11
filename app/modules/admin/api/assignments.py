"""Admin: put staff on a case (or take them off). The case page shows the team and the form."""
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import AdminAccount
from app.modules.staff.models.staff import Staff
from app.platform.services import assignments

router = APIRouter(prefix="/admin/cases", tags=["admin"])


def _back(case_id: str, err: str | None, saved: str) -> RedirectResponse:
    query = urlencode({"error": err, "area": "team"} if err else {"saved": saved, "area": "team"})
    return RedirectResponse(f"/admin/cases/{case_id}?{query}#team", status_code=303)


@router.post("/assign-bulk")
def cases_assign_bulk(account: AdminAccount, db: Session = Depends(get_db), case_ids: list[str] = Form([]),
                      staff_id: str = Form(""), role_code: str = Form(""), next: str = Form("/admin/cases")):
    """Work Queue: give the selected cases to one staff member, then go back to the same view."""
    back = next if next.startswith("/admin/cases") and not next.startswith("//") else "/admin/cases"
    sep = "&" if "?" in back else "?"
    if not case_ids:
        return RedirectResponse(f"{back}{sep}{urlencode({'error': 'Select at least one case to assign.'})}", status_code=303)
    if not staff_id:
        return RedirectResponse(f"{back}{sep}{urlencode({'error': 'Choose who to assign the cases to.'})}", status_code=303)
    done, skipped = assignments.assign_many(db, account.id, case_ids, staff_id, role_code or None)
    staff = db.get(Staff, staff_id)
    who = f"{staff.first_name} {staff.last_name or ''}".strip() if staff else "the staff member"
    params = {}
    if done:
        params["saved"] = f"Assigned {done} case{'s' if done != 1 else ''} to {who}."
    if skipped:
        params["error"] = f"Not assigned ({len(skipped)}): " + "; ".join(skipped[:5]) + (" …" if len(skipped) > 5 else "")
    return RedirectResponse(f"{back}{sep}{urlencode(params)}", status_code=303)


@router.post("/{case_id}/assign")
def case_assign(case_id: str, account: AdminAccount, db: Session = Depends(get_db),
                staff_id: str = Form(""), role_code: str = Form("")):
    err = assignments.assign(db, account.id, case_id, staff_id, role_code or None)
    return _back(case_id, err, "Staff member assigned.")


@router.post("/{case_id}/assignments/{assignment_id}/end")
def case_unassign(case_id: str, assignment_id: str, account: AdminAccount, db: Session = Depends(get_db)):
    err = assignments.unassign(db, account.id, assignment_id, case_id)
    return _back(case_id, err, "Removed from the case.")
