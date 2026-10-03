"""Numbers behind the admin Reports and Analytics tabs. Read-only.

Time in a stage is measured from the latest move into that stage (case_stage_history), or from
when the filing was created if it never moved. Stages marked is_terminal are never "stuck".
"""
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.modules.staff.models.staff import Staff
from app.platform.models.case_workflow import CaseStageHistory
from app.platform.models.filing_document import DocumentComment, FilingDocument
from app.platform.models.lookups import CaseStage
from app.platform.models.tax_filing import TaxFiling
from app.platform.services.activity_feed import aware
from app.platform.services.document_review import account_names


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _days(since: datetime | None, now: datetime) -> float:
    return (now - aware(since)).total_seconds() / 86400 if since else 0.0


def _stages(db: Session) -> list[CaseStage]:
    return db.query(CaseStage).order_by(CaseStage.sort_order, CaseStage.id).all()


def _name(person) -> str:
    return f"{person.first_name} {person.last_name or ''}".strip()


# ── Reports ───────────────────────────────────────────────────────────────────

def filings_by_stage(db: Session) -> dict:
    """Case counts per stage (rows) and tax year (columns)."""
    counts = defaultdict(int)
    for status, year, n in db.query(TaxFiling.status, TaxFiling.tax_year, func.count(TaxFiling.id)).group_by(TaxFiling.status, TaxFiling.tax_year):
        counts[(status, year)] = n
    years = sorted({y for _, y in counts}, reverse=True)
    stages = _stages(db)
    known = {s.code for s in stages}
    rows = [{"code": s.code, "name": s.name, "by_year": [counts[(s.code, y)] for y in years],
             "total": sum(counts[(s.code, y)] for y in years)} for s in stages]
    for code in sorted({c for c, _ in counts} - known):  # statuses no longer in case_stages
        rows.append({"code": code, "name": code.replace("_", " ").title(), "by_year": [counts[(code, y)] for y in years],
                     "total": sum(counts[(code, y)] for y in years)})
    return {"years": years, "rows": rows,
            "year_totals": [sum(n for (_, yy), n in counts.items() if yy == y) for y in years],
            "total": sum(counts.values())}


def _stage_entries(db: Session) -> dict[str, CaseStageHistory]:
    """Latest history row per case (the move into its current stage)."""
    latest: dict[str, CaseStageHistory] = {}
    for h in db.query(CaseStageHistory).order_by(CaseStageHistory.created_at):
        latest[h.case_id] = h
    return latest


def stuck_cases(db: Session, min_days: int = 7) -> list[dict]:
    """Open cases that have been in their current stage for at least min_days."""
    now = _now()
    stages = {s.code: s for s in _stages(db)}
    latest = _stage_entries(db)
    movers = account_names(db, {h.changed_by_account_id for h in latest.values()})
    rows = []
    for f in db.query(TaxFiling).all():
        stage = stages.get(f.status)
        if stage and stage.is_terminal:
            continue
        h = latest.get(f.id)
        entered = h.created_at if h and h.to_stage_code == f.status else f.created_at
        days = _days(entered, now)
        if days < min_days:
            continue
        rows.append({
            "filing_id": f.id, "case_number": f.case_number, "client_name": f"{f.first_name} {f.last_name}",
            "tax_year": f.tax_year, "stage": stage.name if stage else f.status, "days": int(days),
            "since": aware(entered).strftime("%b %d, %Y") if entered else "",
            "moved_by": movers.get(h.changed_by_account_id, {}).get("name") if h and h.to_stage_code == f.status else None,
        })
    rows.sort(key=lambda r: r["days"], reverse=True)
    return rows


def pending_documents(db: Session) -> list[dict]:
    """Latest document versions still waiting for review, oldest first."""
    now = _now()
    q = (
        db.query(FilingDocument, TaxFiling)
        .join(TaxFiling, TaxFiling.id == FilingDocument.filing_id)
        .filter(FilingDocument.is_latest.is_(True), FilingDocument.status == "pending_review")
        .order_by(FilingDocument.uploaded_at)
    )
    return [{
        "filing_id": f.id, "case_number": f.case_number, "client_name": f"{f.first_name} {f.last_name}",
        "document_number": d.document_number, "document_name": d.document_name, "category": d.category,
        "version": d.version, "uploaded": aware(d.uploaded_at).strftime("%b %d, %Y") if d.uploaded_at else "",
        "days": int(_days(d.uploaded_at, now)),
    } for d, f in q]


def parse_range(start: str | None, end: str | None) -> tuple[date, date]:
    """Inclusive date range; defaults to the last 30 days. Swapped if given backwards."""
    today = _now().date()
    try:
        e = date.fromisoformat(end) if end else today
    except ValueError:
        e = today
    try:
        s = date.fromisoformat(start) if start else e - timedelta(days=29)
    except ValueError:
        s = e - timedelta(days=29)
    return (s, e) if s <= e else (e, s)


def staff_activity(db: Session, start: date, end: date) -> list[dict]:
    """Per staff member: stage moves, documents accepted / rejected, and comments, within the range."""
    lo = datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc)
    hi = datetime.combine(end + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)
    lo_n, hi_n = lo.replace(tzinfo=None), hi.replace(tzinfo=None)  # MySQL stores naive UTC
    staff = db.query(Staff).all()
    by_account = {s.account_id: s for s in staff}
    by_staff_id = {s.id: s for s in staff}
    stats = {s.id: {"staff_id": s.id, "name": _name(s), "number": s.staff_number, "moves": 0, "accepted": 0, "rejected": 0, "comments": 0}
             for s in staff}
    for (acc,) in db.query(CaseStageHistory.changed_by_account_id).filter(CaseStageHistory.created_at >= lo_n, CaseStageHistory.created_at < hi_n):
        if acc in by_account:
            stats[by_account[acc].id]["moves"] += 1
    for reviewer, status in db.query(FilingDocument.reviewed_by_id, FilingDocument.status).filter(
            FilingDocument.reviewed_at >= lo_n, FilingDocument.reviewed_at < hi_n):
        s = by_staff_id.get(reviewer) or by_account.get(reviewer)
        if not s:
            continue
        if status == "accepted":
            stats[s.id]["accepted"] += 1
        elif status.startswith("rejected"):
            stats[s.id]["rejected"] += 1
    for (acc,) in db.query(DocumentComment.author_account_id).filter(
            DocumentComment.author_type == "staff", DocumentComment.created_at >= lo_n, DocumentComment.created_at < hi_n):
        if acc in by_account:
            stats[by_account[acc].id]["comments"] += 1
    rows = list(stats.values())
    for r in rows:
        r["total"] = r["moves"] + r["accepted"] + r["rejected"] + r["comments"]
    rows.sort(key=lambda r: (-r["total"], r["name"]))
    return rows


# ── Analytics ─────────────────────────────────────────────────────────────────

def stage_counts(db: Session) -> list[dict]:
    """Current number of cases in each stage, pipeline order."""
    counts = dict(db.query(TaxFiling.status, func.count(TaxFiling.id)).group_by(TaxFiling.status).all())
    return [{"name": s.name, "code": s.code, "count": counts.get(s.code, 0)} for s in _stages(db)]


def client_status_split(db: Session) -> list[dict]:
    """Cases grouped by what the client sees (Submitted / In Progress / Completed / ...)."""
    stage_status = {s.code: s.client_status for s in _stages(db)}
    groups: dict[str, int] = {}
    for status, n in db.query(TaxFiling.status, func.count(TaxFiling.id)).group_by(TaxFiling.status):
        key = stage_status.get(status, "Other")
        groups[key] = groups.get(key, 0) + n
    order = ["Submitted", "In Progress", "Completed", "Amendment", "Other"]
    return [{"label": k, "count": groups[k]} for k in sorted(groups, key=lambda k: order.index(k) if k in order else 99)]


def filings_per_month(db: Session, months: int = 12) -> list[dict]:
    """New filings per calendar month for the last `months` months (oldest first, zero-filled)."""
    today = _now().date().replace(day=1)
    keys = []
    y, m = today.year, today.month
    for _ in range(months):
        keys.append((y, m))
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    keys.reverse()
    first = date(keys[0][0], keys[0][1], 1)
    counts: dict[tuple[int, int], int] = defaultdict(int)
    for (created,) in db.query(TaxFiling.created_at).filter(TaxFiling.created_at >= datetime.combine(first, datetime.min.time())):
        counts[(created.year, created.month)] += 1
    return [{"label": date(yy, mm, 1).strftime("%b %Y"), "short": date(yy, mm, 1).strftime("%b"), "count": counts[(yy, mm)]}
            for yy, mm in keys]


def average_days_per_stage(db: Session) -> list[dict]:
    """Average days a case spent in each stage, from completed stays (moved in, then moved out)."""
    stays: dict[str, list[float]] = defaultdict(list)
    history = defaultdict(list)
    for h in db.query(CaseStageHistory).order_by(CaseStageHistory.created_at):
        history[h.case_id].append(h)
    created = dict(db.query(TaxFiling.id, TaxFiling.created_at).filter(TaxFiling.id.in_(list(history))).all()) if history else {}
    for case_id, moves in history.items():
        # first stay: from filing creation until the first move, in the stage the case was moved out of
        first = moves[0]
        if first.from_stage_code and created.get(case_id):
            stays[first.from_stage_code].append(_days(created[case_id], aware(first.created_at)))
        for prev, nxt in zip(moves, moves[1:]):
            stays[prev.to_stage_code].append(_days(prev.created_at, aware(nxt.created_at)))
    rows = []
    for s in _stages(db):
        vals = stays.get(s.code, [])
        rows.append({"name": s.name, "code": s.code, "cases": len(vals), "avg_days": round(sum(vals) / len(vals), 1) if vals else None})
    return rows


def dashboard_progress(db: Session) -> list[dict]:
    """Share of cases per client-facing status, for the dashboard progress bar."""
    split = client_status_split(db)
    total = sum(r["count"] for r in split)
    return [{**r, "pct": round(100 * r["count"] / total, 1) if total else 0} for r in split if r["count"]]
