import re
from datetime import datetime, timezone
from math import ceil
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.files import send_file
from app.core.deps import CaseStaffAccount, StaffAccount, is_hr
from app.modules.client.api.dashboard import _build_section_statuses
from app.core.templates import templates
from app.platform.models.auth import AuthAccount
from app.platform.models.case_workflow import CaseStageHistory
from app.modules.client.models.client import Client
from app.platform.models.filing_document import DocumentComment, FilingDocument
from app.modules.staff.models.staff import Staff, StaffRole
from app.platform.models.tax_filing import TaxFiling
from app.platform.services import access_log, assignments, case_actions, intake, referrals
from app.platform.services.activity_feed import client_activity
from app.platform.services.catalog import case_stages, document_categories, stage_labels
from app.platform.services.document_review import (REVIEW_CATEGORY, account_names, add_comment, comment_threads, review_documents,
                                                   store_upload)
from app.platform.services.storage_service import save_profile_picture

router = APIRouter(prefix="/staff", tags=["staff"])

def _stages(db: Session) -> list[tuple[str, str]]:
    """Workflow stages in pipeline order, from the case_stages table: (TaxFiling.status, label)."""
    return [(stage.code, stage.name) for stage in case_stages(db)]


def _stage_keys(db: Session) -> list[str]:
    return [code for code, _ in _stages(db)]


def _aware(dt: datetime | None) -> datetime | None:
    """SQLite/MySQL hand back naive datetimes; treat them as UTC so comparisons work."""
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _unread_count(db: Session, staff: Staff | None) -> int:
    """Client activity newer than this staff member's last visit to Notifications."""
    seen = staff.notifications_seen_at if staff else None
    queries = (
        (db.query(func.count(TaxFiling.id)), TaxFiling.created_at),
        (db.query(func.count(FilingDocument.id)).filter(FilingDocument.uploaded_by_type == "client"), FilingDocument.uploaded_at),
        (db.query(func.count(DocumentComment.id)).filter(DocumentComment.author_type == "client", DocumentComment.kind == "reply"), DocumentComment.created_at),
    )
    new = sum((q.filter(col > seen) if seen else q).scalar() or 0 for q, col in queries)
    # Registrations nobody is assigned to yet stay counted until they are (client feedback, Oct 2026)
    if seen:
        waiting = set(assignments.unassigned_case_ids(db))
        new += db.query(func.count(TaxFiling.id)).filter(TaxFiling.id.in_(waiting or [""]), TaxFiling.status == case_actions.REGISTERED_STAGE, TaxFiling.created_at <= seen).scalar() or 0
    return new


def _nav_context(db: Session, staff: Staff | None) -> dict:
    if staff and staff.role == StaffRole.HR:  # no case links or counts in an HR sidebar
        return {"stage_nav": [], "unread_count": 0, "is_hr": True}
    return {"stage_nav": _stage_nav(db), "unread_count": _unread_count(db, staff), "is_hr": False}


def _stage_nav(db: Session) -> list[dict]:
    """Sidebar stage links with a live filing count for each stage."""
    counts = dict(db.query(TaxFiling.status, func.count(TaxFiling.id)).group_by(TaxFiling.status).all())
    return [{"slug": slug, "label": label, "count": counts.get(slug, 0)} for slug, label in _stages(db)]
def _latest_comments(db: Session, case_ids: list[str]) -> dict[str, str]:
    """{case_id: the most recent comment left when the case changed stage}."""
    if not case_ids:
        return {}
    rows = (
        db.query(CaseStageHistory.case_id, CaseStageHistory.comment)
        .filter(CaseStageHistory.case_id.in_(case_ids), CaseStageHistory.comment.isnot(None), CaseStageHistory.comment != "")
        .order_by(CaseStageHistory.created_at)
        .all()
    )
    return {case_id: comment for case_id, comment in rows}  # oldest → newest, so the last one wins


def _intake_context(db: Session, filing: TaxFiling | None) -> dict:
    """What the client saved for the year: Tax Notes (readable), bank details and FBAR (numbers masked), residency,
    dependents and the Form 8879 e-file authorization."""
    notes = intake.section(db, filing, intake.TAX_NOTES)
    bank = intake.section(db, filing, intake.BANK)
    bank_data = dict(bank.data) if bank else None
    if bank_data:
        bank_data["account_masked"] = intake.masked(bank_data.get("account_number"))
    residency = intake.section(db, filing, intake.RESIDENCY)
    dependents = intake.section(db, filing, intake.DEPENDENTS)
    fbar = intake.section(db, filing, intake.FBAR)
    return {
        "tax_notes": intake.notes_summary(notes.data or {}) if notes else None, "tax_notes_row": notes,
        "bank": bank_data, "bank_row": bank, "checklist": intake.checklist(db, filing) if filing else None,
        "residency": residency.data if residency else None, "residency_row": residency,
        "dependents": (dependents.data or {}).get("items") if dependents else None,
        "fbar": intake.fbar_summary(fbar.data if fbar else None), "fbar_row": fbar,
        "efile_row": intake.section(db, filing, intake.EFILE),
    }


def _client_nav(db: Session, filing: TaxFiling, section: str) -> dict:
    """Sidebar context when staff open a client from their readable number (client feedback, Oct 2026):
    tax year switcher, the client number, then links to each part of that client's information."""
    client = db.query(Client).filter(Client.id == filing.client_id).first()
    years = (
        db.query(TaxFiling.tax_year, TaxFiling.id)
        .filter(TaxFiling.client_id == filing.client_id)
        .order_by(TaxFiling.tax_year.desc(), TaxFiling.created_at.desc())
        .all()
    )
    seen: dict[int, str] = {}
    for year, case_id in years:
        seen.setdefault(year, case_id)
    return {
        "client_id": filing.client_id,
        "client_number": client.client_number if client else None,
        "client_name": f"{filing.first_name} {filing.last_name}",
        "case_id": filing.id,
        "tax_year": filing.tax_year,
        "years": list(seen.items()),
        "section": section,
    }


PER_PAGE_OPTIONS = (10, 25, 50, 100)
_SORT_COLUMNS = {
    "created": TaxFiling.created_at,
    "case": TaxFiling.case_number,
    "name": TaxFiling.first_name,
    "customer": Client.client_number,
    "email": TaxFiling.email,
    "year": TaxFiling.tax_year,
    "stage": TaxFiling.status,
}


def _stage_tone(status: str) -> str:
    if status == "complete":
        return "green"
    if status == "rev_doc_rej":
        return "coral"
    if "pending" in status:
        return "amber"
    return "blue"


def _next_stage(status: str, keys: list[str]) -> str | None:
    if not keys:
        return None
    if status not in keys:
        return keys[0]
    i = keys.index(status)
    return keys[i + 1] if i + 1 < len(keys) else None


def _page_window(page: int, pages: int) -> list[int | None]:
    """Page numbers to show, with None marking a gap: 1 … 4 5 6 … 20."""
    keep = {1, pages, page - 1, page, page + 1}
    window: list[int | None] = []
    for n in range(1, pages + 1):
        if n in keep:
            window.append(n)
        elif window and window[-1] is not None:
            window.append(None)
    return window


def _case_table(
    db: Session,
    *,
    q: str | None,
    stage: str | None,
    year: str | None,
    page: int,
    per_page: int,
    sort: str,
    direction: str,
    only_case_ids: list[str] | None = None,
) -> dict:
    """Filtered, sorted, paginated case rows plus the state the case table template needs.

    only_case_ids limits the table to those cases (My Cases: the ones the staff member is assigned to).
    """
    labels = stage_labels(db)
    stage_keys = _stage_keys(db)
    q = (q or "").strip()
    stage = (stage or "").strip()
    year = year if (year and year.isdigit()) else ""
    sort = sort if sort in _SORT_COLUMNS else "created"
    direction = "asc" if direction == "asc" else "desc"
    per_page = per_page if per_page in PER_PAGE_OPTIONS else PER_PAGE_OPTIONS[0]

    query = db.query(TaxFiling, Client.client_number).outerjoin(Client, Client.id == TaxFiling.client_id)
    if q:
        term = f"%{q}%"
        query = query.filter(
            TaxFiling.id.ilike(term)
            | TaxFiling.case_number.ilike(term)
            | TaxFiling.email.ilike(term)
            | Client.client_number.ilike(term)
            | (TaxFiling.first_name + " " + TaxFiling.last_name).ilike(term)
        )
    if stage:
        query = query.filter(TaxFiling.status == stage)
    if year:
        query = query.filter(TaxFiling.tax_year == int(year))
    if only_case_ids is not None:
        query = query.filter(TaxFiling.id.in_(only_case_ids or [""]))

    total = query.count()
    pages = max(1, ceil(total / per_page))
    page = min(max(page, 1), pages)
    offset = (page - 1) * per_page
    column = _SORT_COLUMNS[sort]
    rows = (
        query.order_by(column.asc() if direction == "asc" else column.desc(), TaxFiling.id)
        .offset(offset)
        .limit(per_page)
        .all()
    )

    ids = [f.id for f, _ in rows]
    doc_counts = dict(
        db.query(FilingDocument.filing_id, func.count(FilingDocument.id))
        .filter(FilingDocument.filing_id.in_(ids))
        .group_by(FilingDocument.filing_id)
        .all()
    ) if ids else {}
    teams = assignments.team_names(db, ids)
    comments = _latest_comments(db, ids)

    cases = [
        {
            "sno": offset + i + 1,
            "id": f.id,
            "case_id": f.get_case_id(db),
            "client_id": f.client_id,
            "client_name": f"{f.first_name} {f.last_name}",
            "customer_no": client_number,
            "email": f.email,
            "tax_year": f.tax_year,
            "stage": labels.get(f.status, f.status.replace("_", " ").title()),
            "stage_tone": _stage_tone(f.status),
            "stage_key": f.status,
            "next_stage": _next_stage(f.status, stage_keys),
            "doc_count": doc_counts.get(f.id, 0),
            "staff": ", ".join(teams.get(f.id, [])) or None,
            # A Registered case can't move on until someone is assigned (case_actions.move_stage)
            "needs_assignment": f.status == case_actions.REGISTERED_STAGE and not teams.get(f.id),
            "comment": comments.get(f.id),  # latest stage-move comment
        }
        for i, (f, client_number) in enumerate(rows)
    ]

    state = {"q": q, "stage": stage, "year": year, "sort": sort, "dir": direction, "per_page": per_page, "page": page}

    def url(**changes) -> str:
        params = {**state, **changes}
        return "?" + urlencode({k: v for k, v in params.items() if v not in ("", None)})

    def sort_url(key: str) -> str:
        next_dir = "desc" if (sort == key and direction == "asc") else "asc"
        return url(sort=key, dir=next_dir, page=1)

    years = [y for (y,) in db.query(TaxFiling.tax_year).distinct().order_by(TaxFiling.tax_year.desc()).all()]

    return {
        "cases": cases,
        "mine": only_case_ids is not None,  # My Cases: changes the empty-table hint
        "total": total,
        "page": page,
        "pages": pages,
        "per_page": per_page,
        "per_page_options": PER_PAGE_OPTIONS,
        "start": offset + 1 if total else 0,
        "end": offset + len(cases),
        "page_links": _page_window(page, pages),
        "sort": sort,
        "direction": direction,
        "q": q,
        "stage": stage,
        "stage_label": labels.get(stage, ""),
        "year": year,
        "years": years,
        "url": url,
        "sort_url": sort_url,
    }


def _client_rows(db: Session, year: str | None = None, stage: str | None = None) -> list[dict]:
    """Every client with login email, filing count and the stage of their latest filing.

    year / stage (staff dashboard filters) keep only clients with a filing in that tax year / stage,
    and the row then shows that filing.
    """
    labels = stage_labels(db)
    year = year if (year and str(year).isdigit()) else ""
    stage = (stage or "").strip()
    clients = (
        db.query(Client, AuthAccount.email)
        .outerjoin(AuthAccount, AuthAccount.id == Client.account_id)
        .order_by(Client.created_at.desc())
        .all()
    )
    filing_counts: dict[str, int] = {}
    latest: dict[str, tuple] = {}
    query = db.query(TaxFiling.client_id, TaxFiling.status, TaxFiling.tax_year, TaxFiling.id).order_by(TaxFiling.created_at)
    for client_id, status, tax_year, filing_id in query.all():
        filing_counts[client_id] = filing_counts.get(client_id, 0) + 1
        if (year and tax_year != int(year)) or (stage and status != stage):
            continue
        latest[client_id] = (status, tax_year, filing_id)  # ordered oldest → newest, so the last one wins
    comments = _latest_comments(db, [f for _, _, f in latest.values()])

    rows = []
    for c, email in clients:
        if (year or stage) and c.id not in latest:
            continue
        status, tax_year, filing_id = latest.get(c.id, (None, None, None))
        rows.append({
            "sno": len(rows) + 1,
            "tax_year": tax_year,
            "filing_id": filing_id,
            "comment": comments.get(filing_id),
            "id": c.id,
            "name": f"{c.first_name} {c.last_name or ''}".strip(),
            "customer_no": c.client_number,
            "email": email,
            "phone": c.phone,
            "filings": filing_counts.get(c.id, 0),
            "stage": labels.get(status, status.replace("_", " ").title()) if status else None,
            "stage_tone": _stage_tone(status) if status else None,
            "joined": c.created_at.strftime("%b %d, %Y") if c.created_at else "",
        })
    return rows


@router.get("/dashboard", response_class=HTMLResponse, name="staff_dashboard")
def staff_dashboard(
    request: Request,
    account: StaffAccount,
    q: str | None = None,
    stage: str | None = None,
    year: str | None = None,
    page: int = 1,
    per_page: int = 10,
    sort: str = "created",
    direction: str = Query("desc", alias="dir"),
    db: Session = Depends(get_db),
):
    if is_hr(db, account):  # HR never see cases: their home is their profile
        return RedirectResponse("/staff/profile", status_code=status.HTTP_303_SEE_OTHER)
    staff = db.query(Staff).filter(Staff.account_id == account.id).first()
    stage_nav = _stage_nav(db)
    by_slug = {item["slug"]: item for item in stage_nav}
    years = [y for (y,) in db.query(TaxFiling.tax_year).distinct().order_by(TaxFiling.tax_year.desc()).all()]
    summary_cards = [{"slug": "", "label": "Total cases", "count": db.query(TaxFiling).count()}] + [
        by_slug[slug]
        for slug in ("pending_review", "docs_pending", "payment_pending", "complete")
        if slug in by_slug  # skipped if an admin retires the stage
    ]

    return templates.TemplateResponse(
        request, "staff/index.html",
        {
            "request": request,
            "account": account,
            "staff": staff,
            "app_name": settings.APP_NAME,
            "stage_nav": stage_nav,
            "unread_count": _unread_count(db, staff),
            "active_nav": "dashboard",
            "summary_cards": summary_cards,
            "clients": _client_rows(db, year=year, stage=stage),
            "client_filters": {"year": year if (year and year.isdigit()) else "", "stage": (stage or "").strip(), "years": years},
            "table": _case_table(db, q=q, stage=stage, year=year, page=page, per_page=per_page, sort=sort, direction=direction),
        },
    )


@router.get("/cases/my", response_class=HTMLResponse, name="staff_my_cases")
def staff_my_cases(
    request: Request,
    account: CaseStaffAccount,
    q: str | None = None,
    stage: str | None = None,
    year: str | None = None,
    page: int = 1,
    per_page: int = 10,
    sort: str = "created",
    direction: str = Query("desc", alias="dir"),
    db: Session = Depends(get_db),
):
    staff = db.query(Staff).filter(Staff.account_id == account.id).first()
    return templates.TemplateResponse(
        request, "staff/cases/my_cases.html",
        {
            "request": request,
            "account": account,
            "staff": staff,
            "app_name": settings.APP_NAME,
            **_nav_context(db, staff),
            "active_nav": "my_cases",
            "page_title": "My Cases",
            "selected_stage": (stage or "").strip(),
            "table": _case_table(db, q=q, stage=stage, year=year, page=page, per_page=per_page, sort=sort, direction=direction,
                                  only_case_ids=assignments.case_ids_for(db, staff.id) if staff else []),
        },
    )


@router.get("/cases", response_class=HTMLResponse, name="staff_all_cases")
def staff_all_cases(
    request: Request,
    account: CaseStaffAccount,
    q: str | None = None,
    stage: str | None = None,
    year: str | None = None,
    page: int = 1,
    per_page: int = 10,
    sort: str = "created",
    direction: str = Query("desc", alias="dir"),
    db: Session = Depends(get_db),
):
    staff = db.query(Staff).filter(Staff.account_id == account.id).first()
    return templates.TemplateResponse(
        request, "staff/cases/my_cases.html",
        {
            "request": request,
            "account": account,
            "staff": staff,
            "app_name": settings.APP_NAME,
            **_nav_context(db, staff),
            "active_nav": "all_cases",
            "page_title": "All Cases",
            "selected_stage": (stage or "").strip(),
            "table": _case_table(db, q=q, stage=stage, year=year, page=page, per_page=per_page, sort=sort, direction=direction),
        },
    )


# Labels for the slugs the client intake form stores (see client/new_filing.html)
_CHOICE_LABELS = {
    "us_citizen": "US citizen", "resident_alien": "Resident alien", "nonresident_alien": "Nonresident alien",
    "undetermined": "Not sure", "single": "Single", "married_jointly": "Married filing jointly",
    "married_separately": "Married filing separately", "head_of_household": "Head of household",
    "qualifying_widow": "Qualifying surviving spouse", "individual": "Individual", "business": "Business",
    "estate": "Estate", "non_profit": "Non-profit", "yes": "Yes", "no": "No",
}
_INCOME_LABELS = {
    "w2_employment": "W-2 employment", "self_employment": "Self-employment / business", "freelance": "Freelance / contractor",
    "interest": "Interest", "dividends": "Dividends", "capital_gains": "Capital gains", "rental": "Rental",
    "retirement": "Retirement", "unemployment": "Unemployment", "foreign": "Foreign income",
    "digital_assets": "Digital assets / crypto", "other": "Other income",
}
_DEDUCTION_LABELS = {
    "mortgage_interest": "Mortgage interest", "property_taxes": "Property taxes",
    "charitable_contributions": "Charitable contributions", "education_expenses": "Education expenses",
    "student_loan_interest": "Student loan interest", "child_dependent_care": "Child / dependent care",
    "medical_expenses": "Medical expenses", "retirement_contributions": "Retirement contributions",
    "other": "Other deductions or credits",
}
_SITUATION_LABELS = {
    "lived_outside_us": "Lived outside the US", "multiple_states": "Worked in multiple states",
    "owned_business": "Owned or operated a business", "foreign_income": "Received foreign income",
    "foreign_accounts": "Foreign financial accounts (FBAR)", "digital_assets": "Digital assets",
    "other_special": "Other special situation",
}
_DOC_STATUS = {
    "pending_review": ("Pending review", "amber"),
    "accepted": ("Accepted", "green"),
    "rejected_reupload_requested": ("Re-upload requested", "coral"),
    "superseded": ("Superseded", "blue"),
}


def _label(value, labels: dict = _CHOICE_LABELS) -> str | None:
    if value in (None, ""):
        return None
    return labels.get(value, str(value).replace("_", " ").capitalize())


def _find_filing(db: Session, case_id: str) -> TaxFiling | None:
    """Look up a filing by id, case number or 8-char id prefix. Each lookup is one indexed query."""
    key = case_id.strip()
    filing = db.query(TaxFiling).filter((TaxFiling.id == key) | (TaxFiling.case_number == key.upper())).first()
    if filing or not re.fullmatch(r"[0-9a-fA-F]{8}", key):
        return filing
    return db.query(TaxFiling).filter(TaxFiling.id.like(f"{key.lower()}%")).first()


def _file_size(size: int) -> str:
    for unit in ("B", "KB", "MB"):
        if size < 1024:
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


@router.get("/cases/{case_id}", response_class=HTMLResponse, name="staff_case_detail")
def staff_case_detail(case_id: str, request: Request, account: CaseStaffAccount, db: Session = Depends(get_db)):
    labels = stage_labels(db)
    staff = db.query(Staff).filter(Staff.account_id == account.id).first()
    filing = _find_filing(db, case_id)
    if filing:  # the case page shows bank, FBAR and e-file details
        access_log.record(db, request, account.id, "case_details_viewed", "tax_filing", filing.id)

    case = None
    if filing:
        f = filing
        client = db.query(Client).filter(Client.id == f.client_id).first()
        sections = _build_section_statuses(f)[:8]  # 09 "Documents" is shown from real uploads below
        married = f.filing_status in ("married_jointly", "married_separately")

        docs = (
            db.query(FilingDocument)
            .filter(FilingDocument.filing_id == f.id, FilingDocument.is_latest.is_(True))
            .order_by(FilingDocument.uploaded_at.desc())
            .all()
        )
        documents = [
            {
                "number": d.document_number,
                "name": d.document_name,
                "filename": d.original_filename,
                "category": d.category,
                "version": d.version,
                "size": _file_size(d.file_size or 0),
                "uploaded": d.uploaded_at.strftime("%b %d, %Y") if d.uploaded_at else "",
                "status": _DOC_STATUS.get(d.status, (d.status.replace("_", " ").capitalize(), "blue"))[0],
                "tone": _DOC_STATUS.get(d.status, ("", "blue"))[1],
                "reason": d.rejection_reason,
            }
            for d in docs
        ]
        doc_summary = {label: 0 for label, _ in _DOC_STATUS.values()}
        for d in documents:
            doc_summary[d["status"]] = doc_summary.get(d["status"], 0) + 1

        stages = _stages(db)
        stage_keys = [slug for slug, _ in stages]
        stage_index = stage_keys.index(f.status) if f.status in stage_keys else -1
        pipeline = [
            {
                "label": label,
                "state": "done" if i < stage_index else "current" if i == stage_index else "todo",
            }
            for i, (slug, label) in enumerate(stages)
        ]

        flags = []
        if f.received_irs_notice == "yes":
            flags.append("Client received an IRS notice")
        if f.unresolved_tax_issues == "yes":
            flags.append("Unresolved tax issues reported")
        if f.is_amended_return == "yes":
            flags.append("Amended return requested")
        if "foreign_accounts" in (f.special_situations or []):
            flags.append("Foreign accounts — check FBAR")

        address = ", ".join(
            part for part in (f.address_line_1, f.address_line_2, f.city, f.state_province, f.postal_code, f.country) if part
        )
        complete = sum(1 for s_ in sections if s_["status"] in ("complete", "not_applicable"))

        case = {
            "id": f.id,
            "case_id": f.get_case_id(db),
            "needs_assignment": f.status == case_actions.REGISTERED_STAGE and not assignments.team(db, f.id),
            "team": assignments.team(db, f.id),
            "client_id": f.client_id,
            "client_name": f"{f.first_name} {f.last_name}",
            "client_number": client.client_number if client else None,
            "email": f.email,
            "phone": f.phone,
            "tax_year": f.tax_year,
            "filing_type": _label(f.filing_type),
            "stage": labels.get(f.status, f.status.replace("_", " ").title()),
            "stage_tone": _stage_tone(f.status),
            "stage_number": stage_index + 1,
            "stage_key": f.status,
            "next_stage": _next_stage(f.status, stage_keys),
            "next_stage_label": labels.get(_next_stage(f.status, stage_keys) or ""),
            "stage_total": len(stages),
            "created": f.created_at.strftime("%b %d, %Y") if f.created_at else "",
            "updated": f.updated_at.strftime("%b %d, %Y · %H:%M") if f.updated_at else "",
            "pipeline": pipeline,
            "sections": sections,
            "completeness": round(100 * complete / len(sections)),
            "flags": flags,
            "documents": documents,
            "doc_summary": doc_summary,
            "taxpayer": [
                ("Full name", f"{f.first_name} {f.last_name}"),
                ("Date of birth", f.date_of_birth.strftime("%b %d, %Y") if f.date_of_birth else None),
                ("Email", f.email),
                ("Phone", f.phone),
                ("Citizenship", f.country_of_citizenship),
                ("Country of residence", f.current_country_of_residence),
                ("Address", address or None),
            ],
            "filing_details": [
                ("Tax year", f.tax_year),
                ("Filing type", _label(f.filing_type)),
                ("Filing status", _label(f.filing_status)),
                ("US tax residency", _label(f.us_tax_residency_status)),
                ("Filed US taxes before", _label(f.filed_us_taxes_before)),
                ("Last year filed", f.previous_tax_year_filed),
            ],
            "married": married,
            "spouse": [
                ("Full name", " ".join(p for p in (f.spouse_first_name, f.spouse_last_name) if p) or None),
                ("Date of birth", f.spouse_date_of_birth.strftime("%b %d, %Y") if f.spouse_date_of_birth else None),
                ("Email", f.spouse_email),
                ("Phone", f.spouse_phone),
                ("Citizenship", f.spouse_country_of_citizenship),
                ("US tax residency", _label(f.spouse_us_tax_residency_status)),
            ],
            "dependents": f.dependents or [],
            "income": [_label(v, _INCOME_LABELS) for v in (f.income_categories or [])],
            "deductions": [_label(v, _DEDUCTION_LABELS) for v in (f.deductions_credits or [])],
            "situations": [_label(v, _SITUATION_LABELS) for v in (f.special_situations or [])],
            "previous": [
                ("Has a copy of last return", f.has_previous_return_copy),
                ("Received an IRS notice", f.received_irs_notice),
                ("Unresolved tax issues", f.unresolved_tax_issues),
                ("Amended return", f.is_amended_return),
            ],
        }

    return templates.TemplateResponse(
        request, "staff/cases/detail.html",
        {
            "request": request,
            "account": account,
            "staff": staff,
            "case": case,
            "client_nav": _client_nav(db, filing, "case") if filing else None,
            # Saved by the client on Tax Notes / Bank Details (client feedback, Oct 2026)
            **_intake_context(db, filing),
            "app_name": settings.APP_NAME,
            **_nav_context(db, staff),
        },
    )


def _doc_row(d: FilingDocument, filing_id: str, names: dict[str, dict] | None = None) -> dict:
    label, tone = _DOC_STATUS.get(d.status, (d.status.replace("_", " ").capitalize(), "blue"))
    mime = d.mime_type or ""
    kind = "pdf" if mime == "application/pdf" else "image" if mime.startswith("image/") else "other"
    return {
        "id": d.id,
        "group_id": d.document_group_id,
        "number": d.document_number,
        "name": d.document_name,
        "filename": d.original_filename,
        "category": d.category,
        "version": d.version,
        "size": _file_size(d.file_size or 0),
        "mime": mime,
        "kind": kind,
        "ext": (d.original_filename.rsplit(".", 1)[-1] if "." in d.original_filename else "file").upper()[:4],
        "status_key": d.status,
        "status": label,
        "tone": tone,
        "reason": d.rejection_reason,
        "uploaded": d.uploaded_at.strftime("%b %d, %Y · %H:%M") if d.uploaded_at else "",
        "uploaded_by": (names or {}).get(d.uploaded_by_id, {}).get("name") or d.uploaded_by_type.capitalize(),
        "uploaded_role": (names or {}).get(d.uploaded_by_id, {}).get("role") or d.uploaded_by_type.lower(),
        "reviewed": d.reviewed_at.strftime("%b %d, %Y · %H:%M") if d.reviewed_at else None,
        "url": f"/staff/cases/{filing_id}/documents/{d.id}/file",
    }


@router.get("/cases/{case_id}/documents", response_class=HTMLResponse, name="staff_case_documents")
def staff_case_documents(
    case_id: str,
    request: Request,
    account: CaseStaffAccount,
    doc: str | None = None,
    error: str | None = None,
    db: Session = Depends(get_db),
):
    labels = stage_labels(db)
    staff = db.query(Staff).filter(Staff.account_id == account.id).first()
    filing = _find_filing(db, case_id)

    case = None
    groups: list[dict] = []
    rows: list[dict] = []
    if filing:
        case = {
            "id": filing.id,
            "case_id": filing.get_case_id(db),
            "client_id": filing.client_id,
            "client_name": f"{filing.first_name} {filing.last_name}",
            "tax_year": filing.tax_year,
            "stage": labels.get(filing.status, filing.status.replace("_", " ").title()),
            "stage_tone": _stage_tone(filing.status),
        }
        all_docs = (
            db.query(FilingDocument)
            .filter(FilingDocument.filing_id == filing.id, FilingDocument.category != REVIEW_CATEGORY)  # review docs: own section
            .order_by(FilingDocument.uploaded_at.desc())
            .all()
        )
        names = account_names(db, {d.uploaded_by_id for d in all_docs})
        threads = comment_threads(db, filing.id)
        versions: dict[str, list[dict]] = {}
        for d in all_docs:
            versions.setdefault(d.document_group_id, []).append(_doc_row(d, filing.id, names))

        latest = [_doc_row(d, filing.id, names) for d in all_docs if d.is_latest]
        for row in latest:
            row["versions"] = sorted(versions.get(row["group_id"], []), key=lambda v: v["version"], reverse=True)
            row["comments"] = [
                {
                    "author": c.author_name,
                    "role": c.author_type,
                    "kind": c.kind,
                    "body": c.body,
                    "time": _aware(c.created_at).strftime("%b %d, %Y · %H:%M"),
                }
                for c in threads.get(row["group_id"], [])
            ]
            # Rejections made before comments existed only have the reason field
            if not row["comments"] and row["reason"]:
                row["comments"] = [{"author": "Tax team", "role": "staff", "kind": "rejection", "body": row["reason"], "time": ""}]
            row["client_replied"] = bool(row["comments"]) and row["comments"][-1]["role"] == "client"

        categories = document_categories(db)
        order = {c: i for i, c in enumerate(categories)}
        by_category: dict[str, list[dict]] = {}
        for row in latest:
            row["category_label"] = categories.get(row["category"], row["category"].replace("_", " "))
            by_category.setdefault(row["category"], []).append(row)
        groups = [
            {"category": c, "docs": by_category[c]}
            for c in sorted(by_category, key=lambda c: (order.get(c, len(order)), c))
        ]

        # Table rows: each current document, followed by its older versions (hidden until toggled)
        for g in groups:
            for row in g["docs"]:
                row["is_old"] = False
                rows.append(row)
                for old in row["versions"]:
                    if old["id"] != row["id"]:
                        rows.append({**old, "is_old": True, "versions": []})

    counts = {"all": 0, "pending_review": 0, "accepted": 0, "rejected_reupload_requested": 0}
    for g in groups:
        for r in g["docs"]:
            counts["all"] += 1
            if r["status_key"] in counts:
                counts[r["status_key"]] += 1

    return templates.TemplateResponse(
        request, "staff/cases/documents.html",
        {
            "request": request,
            "account": account,
            "staff": staff,
            "case": case,
            "rows": rows,
            "highlight": doc,
            "counts": counts,
            "error": error,
            "review_docs": review_documents(db, filing.id) if filing else [],
            "client_nav": _client_nav(db, filing, "documents") if filing else None,
            "checklist": intake.checklist(db, filing) if filing else None,
            "app_name": settings.APP_NAME,
            **_nav_context(db, staff),
        },
    )


@router.get("/cases/{filing_id}/documents/{doc_id}/file", name="staff_case_document_file")
def staff_case_document_file(
    filing_id: str,
    doc_id: str,
    request: Request,
    account: CaseStaffAccount,
    download: bool = False,
    db: Session = Depends(get_db),
):
    """Serve an uploaded case document to staff — inline for preview, or as a download."""
    d = db.query(FilingDocument).filter(FilingDocument.id == doc_id, FilingDocument.filing_id == filing_id).first()
    if not d:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found.")
    access_log.record(db, request, account.id, "document_downloaded" if download else "document_viewed", "filing_document", d.id,
                      case_id=filing_id, name=d.original_filename)
    return send_file(d.file_path, d.original_filename, d.mime_type, inline=not download)


@router.post("/cases/{filing_id}/documents/{doc_id}/review", name="staff_review_document")
def staff_review_document(
    filing_id: str,
    doc_id: str,
    account: CaseStaffAccount,
    action: str = Form(...),
    reason: str = Form(""),
    db: Session = Depends(get_db),
):
    """Accept a document, or send it back to the client with a reason to re-upload."""
    d = db.query(FilingDocument).filter(FilingDocument.id == doc_id, FilingDocument.filing_id == filing_id).first()
    if not d:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found.")
    back = f"/staff/cases/{filing_id}/documents?doc={doc_id}"
    staff = db.query(Staff).filter(Staff.account_id == account.id).first()
    err = case_actions.review_document(db, d, action, reason, actor_account_id=account.id,
                                       reviewer_id=staff.id if staff else account.id, author_type="staff")
    if err == "reason":
        return RedirectResponse(f"{back}&error=reason", status_code=status.HTTP_303_SEE_OTHER)
    if err:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unknown review action.")
    return RedirectResponse(back, status_code=status.HTTP_303_SEE_OTHER)


@router.post("/cases/{filing_id}/review-documents", name="staff_upload_review_document")
def staff_upload_review_document(
    filing_id: str,
    account: CaseStaffAccount,
    file: UploadFile = File(...),
    document_name: str = Form(""),
    document_group_id: str = Form(""),
    note: str = Form(""),
    db: Session = Depends(get_db),
):
    """Send a filled-in document (draft return, form) to the client to approve or reject on Review Documents.

    With document_group_id it replaces a document the client rejected, as its next version.
    """
    filing = db.query(TaxFiling).filter(TaxFiling.id == filing_id).first()
    client = db.query(Client).filter(Client.id == filing.client_id).first() if filing else None
    if not filing or not client:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found.")
    back = f"/staff/cases/{filing_id}/documents#review-docs"
    if not file.filename:
        return RedirectResponse(f"/staff/cases/{filing_id}/documents?error=review_file#review-docs", status_code=status.HTTP_303_SEE_OTHER)
    group = document_group_id.strip() or None
    if group and not db.query(FilingDocument).filter(FilingDocument.filing_id == filing_id, FilingDocument.document_group_id == group,
                                                     FilingDocument.category == REVIEW_CATEGORY).first():
        group = None
    doc = store_upload(db, client=client, filing=filing, file=file, category=REVIEW_CATEGORY,
                       document_name=document_name.strip()[:120] or None, document_group_id=group,
                       uploaded_by_type="staff", uploaded_by_id=account.id)
    doc.status = "client_review_pending"
    db.flush()
    if note.strip():
        add_comment(db, document=doc, kind="note", body=note, author_type="staff", author_account_id=account.id)
    db.commit()
    return RedirectResponse(back, status_code=status.HTTP_303_SEE_OTHER)


@router.post("/cases/{filing_id}/documents/{doc_id}/comments", name="staff_comment_document")
def staff_comment_document(
    filing_id: str,
    doc_id: str,
    account: CaseStaffAccount,
    body: str = Form(""),
    db: Session = Depends(get_db),
):
    """Staff reply in a document's conversation with the client."""
    d = db.query(FilingDocument).filter(FilingDocument.id == doc_id, FilingDocument.filing_id == filing_id).first()
    if not d:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found.")
    case_actions.comment_on_document(db, d, body, author_type="staff", actor_account_id=account.id)
    return RedirectResponse(f"/staff/cases/{filing_id}/documents?doc={doc_id}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/cases/{filing_id}/stage", name="staff_move_stage")
def staff_move_stage(
    filing_id: str,
    account: CaseStaffAccount,
    to: str = Form(...),
    next: str = Form("/staff/cases"),
    comment: str = Form(""),
    db: Session = Depends(get_db),
):
    """Move a filing to another workflow stage, then return to the list it was moved from."""
    filing = db.query(TaxFiling).filter(TaxFiling.id == filing_id).first()
    if not filing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Case not found.")
    # Only redirect within the staff portal
    back = next if next.startswith("/staff/") and not next.startswith("//") else "/staff/cases"
    err = case_actions.move_stage(db, filing, to, account.id, comment=comment)
    if err == case_actions.NOT_ASSIGNED_ERROR:
        sep = "&" if "?" in back else "?"
        return RedirectResponse(f"{back}{sep}{urlencode({'move_error': err})}", status_code=status.HTTP_303_SEE_OTHER)
    if err:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unknown stage.")
    return RedirectResponse(back, status_code=status.HTTP_303_SEE_OTHER)


def _notification_feed(db: Session, limit: int = 300) -> list[dict]:
    """Recent client activity: new filings, uploads / re-uploads, and replies on documents."""
    return client_activity(
        db,
        case_link=lambda f: f"/staff/cases/{f.id}",
        doc_link=lambda f, doc_id: f"/staff/cases/{f.id}/documents" + (f"?doc={doc_id}" if doc_id else ""),
        limit=limit,
    )


@router.get("/clients", response_class=HTMLResponse, name="staff_clients")
def staff_clients(request: Request, account: CaseStaffAccount, db: Session = Depends(get_db)):
    staff = db.query(Staff).filter(Staff.account_id == account.id).first()
    clients = _client_rows(db)
    return templates.TemplateResponse(
        request, "staff/clients/index.html",
        {"request": request, "account": account, "staff": staff, "clients": clients, "app_name": settings.APP_NAME, **_nav_context(db, staff)},
    )


@router.get("/clients/{client_id}", response_class=HTMLResponse, name="staff_client_detail")
def staff_client_detail(client_id: str, request: Request, account: CaseStaffAccount, db: Session = Depends(get_db)):
    labels = stage_labels(db)
    staff = db.query(Staff).filter(Staff.account_id == account.id).first()
    
    # Lookup client by ID or client_number
    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        client = db.query(Client).filter(Client.client_number == client_id).first()

    if not client:
        # Fallback if client record wasn't created separately
        filing = db.query(TaxFiling).filter(TaxFiling.client_id == client_id).first()
        if filing:
            client = Client(
                id=filing.client_id,
                client_number="—",  # display-only stand-in; never show another client's number
                first_name=filing.first_name,
                last_name=filing.last_name,
                phone=filing.phone,
                date_of_birth=filing.date_of_birth,
                address_line_1=filing.address_line_1,
                address_line_2=filing.address_line_2,
                city=filing.city,
                state_province=filing.state_province,
                postal_code=filing.postal_code,
                country=filing.country,
            )

    filings = []
    client_email = None
    if client:
        records = (
            db.query(TaxFiling)
            .filter(TaxFiling.client_id == client.id)
            .order_by(TaxFiling.tax_year.desc(), TaxFiling.created_at.desc())
            .all()
        )
        ids = [f.id for f in records]
        doc_counts = dict(
            db.query(FilingDocument.filing_id, func.count(FilingDocument.id))
            .filter(FilingDocument.filing_id.in_(ids))
            .group_by(FilingDocument.filing_id)
            .all()
        ) if ids else {}
        filings = [
            {
                "id": f.id,
                "case_id": f.get_case_id(db),
                "tax_year": f.tax_year,
                "filing_type": f.filing_type,
                "stage": labels.get(f.status, f.status.replace("_", " ").title()),
                "stage_tone": _stage_tone(f.status),
                "doc_count": doc_counts.get(f.id, 0),
                "submitted": f.created_at.strftime("%b %d, %Y") if f.created_at else "",
                "updated": f.updated_at.strftime("%b %d, %Y") if f.updated_at else "",
            }
            for f in records
        ]
        client_email = db.query(AuthAccount.email).filter(AuthAccount.id == client.account_id).scalar()
        # Fallback client (no Client row) only has the filing's email
        if not client_email and records:
            client_email = records[0].email

    return templates.TemplateResponse(
        request, "staff/clients/detail.html",
        {
            "request": request,
            "account": account,
            "staff": staff,
            "client": client,
            "client_email": client_email,
            "filings": filings,
            "app_name": settings.APP_NAME,
            **_nav_context(db, staff),
        },
    )


@router.get("/notifications", response_class=HTMLResponse, name="staff_notifications")
def staff_notifications(request: Request, account: CaseStaffAccount, db: Session = Depends(get_db)):
    staff = db.query(Staff).filter(Staff.account_id == account.id).first()
    seen = _aware(staff.notifications_seen_at) if staff else None
    events = _notification_feed(db)
    teams = assignments.team_names(db, list({e["filing_id"] for e in events if e.get("filing_id")}))
    for e in events:
        e["assigned"] = teams.get(e.get("filing_id"), [])
        # New since the last visit, or a registration nobody has been assigned to yet: those stay
        # "Latest" until the case is assigned (client feedback, Oct 2026).
        e["is_new"] = bool(e["at"]) and (seen is None or e["at"] > seen) or (e["kind"] == "filing" and not e["assigned"] and e["filing"].status == case_actions.REGISTERED_STAGE)
    counts = {k: sum(1 for e in events if e["kind"] == k) for k in ("filing", "upload", "reupload", "reply")}
    new_count = sum(1 for e in events if e["is_new"])

    response = templates.TemplateResponse(
        request, "staff/notifications.html",
        {
            "request": request,
            "account": account,
            "staff": staff,
            "events": events,
            "counts": counts,
            "new_count": new_count,
            "app_name": settings.APP_NAME,
            "stage_nav": _stage_nav(db),
            "unread_count": 0,  # this visit marks everything as read
        },
    )
    # Opening the page marks everything up to now as read
    if staff:
        staff.notifications_seen_at = datetime.now(timezone.utc)
        db.commit()
    return response


@router.get("/profile", response_class=HTMLResponse, name="staff_profile")
def staff_profile(request: Request, account: StaffAccount, db: Session = Depends(get_db)):
    staff = db.query(Staff).filter(Staff.account_id == account.id).first()
    return templates.TemplateResponse(
        request, "staff/profile.html",
        {"request": request, "account": account, "staff": staff, "app_name": settings.APP_NAME, **_nav_context(db, staff)},
    )


@router.post("/profile/avatar")
def upload_staff_avatar(
    account: StaffAccount,
    file: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    staff = db.query(Staff).filter(Staff.account_id == account.id).first()
    if not staff:
        raise HTTPException(status_code=404, detail="Staff account not found")
    avatar_url = save_profile_picture(file, staff.id, "staff")
    staff.profile_picture = avatar_url
    db.commit()
    return {"message": "Staff profile picture updated successfully!", "avatar_url": avatar_url}


# ── Referrals (client feedback, Oct 2026): follow up colleagues that clients refer ──────────────

def _referral_back(path: str, err: str | None, saved: str) -> RedirectResponse:
    return RedirectResponse(f"{path}{'&' if '?' in path else '?'}{urlencode({'error': err} if err else {'saved': saved})}",
                            status_code=status.HTTP_303_SEE_OTHER)


@router.get("/referrals", response_class=HTMLResponse, name="staff_referrals")
def staff_referrals(request: Request, account: CaseStaffAccount, db: Session = Depends(get_db), status_filter: str = Query("all", alias="status"),
                    q: str = "", mine: bool = False, due: bool = False, source: str = "", saved: str = "", error: str = ""):
    staff = db.query(Staff).filter(Staff.account_id == account.id).first()
    mine_id = staff.id if (mine and staff) else None
    return templates.TemplateResponse(request, "staff/referrals/index.html", {
        "request": request, "account": account, "staff": staff, "app_name": settings.APP_NAME, **_nav_context(db, staff),
        "active_nav": "referrals", "rows": referrals.list_leads(db, status=status_filter, q=q, staff_id=mine_id, due=due, source=source),
        "counts": referrals.counts(db, staff_id=mine_id), "status": status_filter, "q": q, "mine": mine, "due": due,
        "statuses": referrals.STATUSES, "relationships": referrals.RELATIONSHIPS, "saved": saved, "error": error,
        "source": source, "sources": referrals.SOURCES,
    })


@router.post("/referrals", name="staff_add_referral")
def staff_add_referral(account: CaseStaffAccount, db: Session = Depends(get_db), referrer: str = Form(""), name: str = Form(""),
                       phone: str = Form(""), email: str = Form(""), relationship: str = Form(""), note: str = Form("")):
    """Staff add a colleague a client mentioned (e.g. on a call), on that client's behalf."""
    client = referrals.find_client(db, referrer)
    if referrer.strip() and not client:
        return _referral_back("/staff/referrals?status=open", f"No client found for {referrer.strip()}. Use their client number or email.", "")
    err = referrals.add_lead(db, referrer=client, name=name, phone=phone, email=email, relationship=relationship, note=note,
                             source="staff", actor_account_id=account.id)
    return _referral_back("/staff/referrals?status=open", err, f"{' '.join(name.split())} added to referrals.")


@router.get("/referrals/{lead_id}", response_class=HTMLResponse, name="staff_referral_detail")
def staff_referral_detail(lead_id: str, request: Request, account: CaseStaffAccount, db: Session = Depends(get_db), saved: str = "", error: str = ""):
    staff = db.query(Staff).filter(Staff.account_id == account.id).first()
    row = referrals.lead_detail(db, lead_id)
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Referral not found.")
    return templates.TemplateResponse(request, "staff/referrals/detail.html", {
        "request": request, "account": account, "staff": staff, "app_name": settings.APP_NAME, **_nav_context(db, staff),
        "active_nav": "referrals", "r": row, "outcomes": referrals.CALL_OUTCOMES, "statuses": referrals.MANUAL_STATUSES,
        "status_label": referrals.STATUS_LABEL, "outcome_label": referrals.OUTCOME_LABEL, "saved": saved, "error": error,
        "today": datetime.now(timezone.utc).date().isoformat(),
    })


@router.post("/referrals/{lead_id}/log", name="staff_referral_log")
def staff_referral_log(lead_id: str, account: CaseStaffAccount, db: Session = Depends(get_db), outcome: str = Form(""), note: str = Form(""),
                       lead_status: str = Form("", alias="status"), next_follow_up: str = Form("")):
    lead = db.get(referrals.ReferralLead, lead_id)
    if not lead:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Referral not found.")
    err = referrals.log_followup(db, lead, actor_account_id=account.id, actor_role="staff", outcome=outcome, note=note,
                                 status=lead_status, next_follow_up=next_follow_up)
    return _referral_back(f"/staff/referrals/{lead_id}", err, "Follow-up saved.")
