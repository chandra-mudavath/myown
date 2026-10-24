"""Admin Referrals (client feedback, Oct 2026): every referred colleague, who's following them up, and the call log.
Admins assign each referral to a staff member; staff (and admins) call and log the outcome."""
from datetime import date
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import AdminAccount
from app.core.templates import templates
from app.modules.admin.services.profile_service import current_admin
from app.platform.models.referral import ReferralLead
from app.platform.services import referrals

router = APIRouter(prefix="/admin/referrals", tags=["admin"])


def _ctx(request: Request, account, db: Session, **extra) -> dict:
    return {"request": request, "account": account, "admin": current_admin(db, account), "app_name": settings.APP_NAME,
            "active_nav": "referrals", **extra}


def _back(path: str, err: str | None, saved: str) -> RedirectResponse:
    sep = "&" if "?" in path else "?"
    return RedirectResponse(f"{path}{sep}{urlencode({'error': err} if err else {'saved': saved})}", status_code=303)


def _lead(db: Session, lead_id: str) -> ReferralLead:
    lead = db.get(ReferralLead, lead_id)
    if not lead:
        raise HTTPException(status_code=404, detail="Referral not found.")
    return lead


@router.get("", response_class=HTMLResponse, name="admin_referrals")
def referrals_home(request: Request, account: AdminAccount, db: Session = Depends(get_db), status: str = "all", q: str = "",
                   staff: str = "", due: bool = False, source: str = "", saved: str = "", error: str = ""):
    rows = referrals.list_leads(db, status=status, q=q, due=due, source=source)
    if staff == "none":
        rows = [r for r in rows if not r["lead"].assigned_staff_id]
    elif staff:
        rows = [r for r in rows if r["lead"].assigned_staff_id == staff]
    return templates.TemplateResponse(request, "admin/referrals/index.html", _ctx(
        request, account, db, rows=rows, counts=referrals.counts(db), status=status, q=q, staff=staff, due=due,
        source=source, sources=referrals.SOURCES,
        staff_options=referrals.staff_options(db), relationships=referrals.RELATIONSHIPS, saved=saved, error=error))


@router.post("", name="admin_add_referral")
def add_referral(account: AdminAccount, db: Session = Depends(get_db), referrer: str = Form(""), name: str = Form(""), phone: str = Form(""),
                 email: str = Form(""), relationship: str = Form(""), note: str = Form(""), staff_id: str = Form("")):
    client = referrals.find_client(db, referrer)
    if referrer.strip() and not client:
        return _back("/admin/referrals", f"No client found for {referrer.strip()}. Use their client number or email.", "")
    err = referrals.add_lead(db, referrer=client, name=name, phone=phone, email=email, relationship=relationship, note=note,
                             source="admin", actor_account_id=account.id)
    if not err and staff_id:
        lead = db.query(ReferralLead).filter(ReferralLead.created_by_account_id == account.id).order_by(ReferralLead.created_at.desc()).first()
        if lead:
            referrals.assign(db, lead, staff_id, account.id)
    return _back("/admin/referrals", err, f"{' '.join(name.split())} added to referrals.")


@router.post("/assign", name="admin_assign_referrals")
def assign_many(account: AdminAccount, db: Session = Depends(get_db), lead_ids: list[str] = Form([]), staff_id: str = Form("")):
    """Assign the ticked referrals to one staff member (or unassign with an empty choice)."""
    if not lead_ids:
        return _back("/admin/referrals", "Tick at least one referral to assign.", "")
    for lead_id in dict.fromkeys(lead_ids):
        lead = db.get(ReferralLead, lead_id)
        if lead:
            err = referrals.assign(db, lead, staff_id, account.id)
            if err:
                return _back("/admin/referrals", err, "")
    return _back("/admin/referrals", None, f"{len(lead_ids)} referral{'s' if len(lead_ids) != 1 else ''} updated.")


@router.get("/{lead_id}", response_class=HTMLResponse, name="admin_referral_detail")
def referral_detail(lead_id: str, request: Request, account: AdminAccount, db: Session = Depends(get_db), saved: str = "", error: str = ""):
    row = referrals.lead_detail(db, lead_id)
    if not row:
        raise HTTPException(status_code=404, detail="Referral not found.")
    return templates.TemplateResponse(request, "admin/referrals/detail.html", _ctx(
        request, account, db, r=row, outcomes=referrals.CALL_OUTCOMES, statuses=referrals.MANUAL_STATUSES,
        outcome_label=referrals.OUTCOME_LABEL, staff_options=referrals.staff_options(db), saved=saved, error=error,
        today=date.today().isoformat()))


@router.post("/{lead_id}/assign", name="admin_assign_referral")
def assign_one(lead_id: str, account: AdminAccount, db: Session = Depends(get_db), staff_id: str = Form("")):
    err = referrals.assign(db, _lead(db, lead_id), staff_id, account.id)
    return _back(f"/admin/referrals/{lead_id}", err, "Assignment saved.")


@router.post("/{lead_id}/log", name="admin_referral_log")
def log(lead_id: str, account: AdminAccount, db: Session = Depends(get_db), outcome: str = Form(""), note: str = Form(""),
        status: str = Form(""), next_follow_up: str = Form("")):
    err = referrals.log_followup(db, _lead(db, lead_id), actor_account_id=account.id, actor_role="admin", outcome=outcome, note=note,
                                 status=status, next_follow_up=next_follow_up)
    return _back(f"/admin/referrals/{lead_id}", err, "Follow-up saved.")
