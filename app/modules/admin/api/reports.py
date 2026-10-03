"""Admin Reports (tables + CSV export) and Analytics (charts). Read-only."""
import csv
import io

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import AdminAccount
from app.core.templates import templates
from app.modules.admin.services import reports as rpt
from app.modules.admin.services.profile_service import current_admin

router = APIRouter(prefix="/admin", tags=["admin"])


def _clamp_days(days: int) -> int:
    return max(0, min(days, 365))


@router.get("/reports", response_class=HTMLResponse, name="admin_reports")
def admin_reports(request: Request, account: AdminAccount, db: Session = Depends(get_db),
                  days: int = 7, start: str = "", end: str = ""):
    days = _clamp_days(days)
    s, e = rpt.parse_range(start, end)
    return templates.TemplateResponse("admin/reports.html", {
        "request": request, "account": account, "admin": current_admin(db, account), "app_name": settings.APP_NAME,
        "active_nav": "reports", "days": days, "start": s.isoformat(), "end": e.isoformat(),
        "by_stage": rpt.filings_by_stage(db), "stuck": rpt.stuck_cases(db, days),
        "pending": rpt.pending_documents(db), "activity": rpt.staff_activity(db, s, e),
    })


def _safe(value) -> str:
    """Stop spreadsheet apps from running a cell as a formula (client-entered names end up here)."""
    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def _csv(filename: str, header: list[str], rows: list[list]) -> Response:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    for r in rows:
        w.writerow([_safe(v) for v in r])
    return Response("﻿" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/reports/{report}.csv", name="admin_report_csv")
def admin_report_csv(report: str, account: AdminAccount, db: Session = Depends(get_db),
                     days: int = 7, start: str = "", end: str = ""):
    if report == "filings-by-stage":
        data = rpt.filings_by_stage(db)
        return _csv("filings-by-stage.csv", ["Stage", "Code", *map(str, data["years"]), "Total"],
                    [[r["name"], r["code"], *r["by_year"], r["total"]] for r in data["rows"]])
    if report == "stuck-cases":
        days = _clamp_days(days)
        return _csv(f"stuck-cases-{days}d.csv", ["Case", "Client", "Tax year", "Stage", "In stage since", "Days", "Moved by"],
                    [[r["case_number"], r["client_name"], r["tax_year"], r["stage"], r["since"], r["days"], r["moved_by"] or ""]
                     for r in rpt.stuck_cases(db, days)])
    if report == "pending-documents":
        return _csv("documents-awaiting-review.csv", ["Document", "Name", "Category", "Version", "Case", "Client", "Uploaded", "Days waiting"],
                    [[r["document_number"], r["document_name"], r["category"], r["version"], r["case_number"], r["client_name"], r["uploaded"], r["days"]]
                     for r in rpt.pending_documents(db)])
    if report == "staff-activity":
        s, e = rpt.parse_range(start, end)
        return _csv(f"staff-activity-{s}-to-{e}.csv", ["Staff", "Staff #", "Stage moves", "Docs accepted", "Docs rejected", "Comments", "Total"],
                    [[r["name"], r["number"], r["moves"], r["accepted"], r["rejected"], r["comments"], r["total"]]
                     for r in rpt.staff_activity(db, s, e)])
    raise HTTPException(status_code=404, detail="Unknown report")


@router.get("/analytics", response_class=HTMLResponse, name="admin_analytics")
def admin_analytics(request: Request, account: AdminAccount, db: Session = Depends(get_db)):
    stages = rpt.stage_counts(db)
    months = rpt.filings_per_month(db)
    avg = rpt.average_days_per_stage(db)
    split = rpt.client_status_split(db)
    return templates.TemplateResponse("admin/analytics.html", {
        "request": request, "account": account, "admin": current_admin(db, account), "app_name": settings.APP_NAME,
        "active_nav": "analytics", "stages": stages, "stage_max": max([r["count"] for r in stages] or [0]) or 1,
        "months": months, "month_max": max([r["count"] for r in months] or [0]) or 1,
        "avg": avg, "avg_max": max([r["avg_days"] or 0 for r in avg] or [0]) or 1,
        "split": split, "split_total": sum(r["count"] for r in split),
        "total_cases": sum(r["count"] for r in stages), "new_this_month": months[-1]["count"] if months else 0,
        "stuck_7": len(rpt.stuck_cases(db, 7)), "pending_docs": len(rpt.pending_documents(db)),
    })
