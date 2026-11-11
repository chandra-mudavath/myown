"""Activity feeds for the staff and admin notification pages.

client_activity() is what staff see: new filings, client uploads / re-uploads and client replies.
Admin see the same plus stage moves and new accounts (internal_activity()). Each portal passes its
own link builders, so the events point at that portal's pages.
"""
from collections.abc import Callable
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.modules.client.models.client import Client
from app.platform.models.auth import AccountType, AuthAccount
from app.platform.models.case_workflow import CaseStageHistory
from app.platform.models.filing_document import DocumentComment, FilingDocument
from app.platform.models.tax_filing import TaxFiling
from app.platform.services.catalog import stage_labels
from app.platform.services.document_review import account_names

CaseLink = Callable[[TaxFiling], str]
DocLink = Callable[[TaxFiling, str | None], str]

_EPOCH = datetime.min.replace(tzinfo=timezone.utc)


def aware(dt: datetime | None) -> datetime | None:
    """SQLite/MySQL hand back naive datetimes; treat them as UTC so comparisons work."""
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _filing_events(db: Session, case_link: CaseLink, doc_link: DocLink, limit: int) -> list[dict]:
    events: list[dict] = []
    for f in db.query(TaxFiling).order_by(TaxFiling.created_at.desc()).limit(limit).all():
        events.append({
            "at": aware(f.created_at), "kind": "filing", "label": "New filing",
            "filing": f, "detail": f"Tax year {f.tax_year} · {f.filing_type.capitalize()} filing submitted",
            "href": case_link(f),
        })
    uploads = (
        db.query(FilingDocument, TaxFiling)
        .join(TaxFiling, TaxFiling.id == FilingDocument.filing_id)
        .filter(FilingDocument.uploaded_by_type == "client")
        .order_by(FilingDocument.uploaded_at.desc())
        .limit(limit)
        .all()
    )
    for d, f in uploads:
        reupload = d.version > 1
        events.append({
            "at": aware(d.uploaded_at), "kind": "reupload" if reupload else "upload",
            "label": "Re-uploaded" if reupload else "Uploaded",
            "filing": f, "detail": f"{d.document_name} ({d.original_filename}){f' · v{d.version}' if reupload else ''}",
            "href": doc_link(f, d.id),
        })
    replies = (
        db.query(DocumentComment, TaxFiling)
        .join(TaxFiling, TaxFiling.id == DocumentComment.filing_id)
        .filter(DocumentComment.author_type == "client", DocumentComment.kind == "reply")
        .order_by(DocumentComment.created_at.desc())
        .limit(limit)
        .all()
    )
    for c, f in replies:
        events.append({
            "at": aware(c.created_at), "kind": "reply", "label": "Replied",
            "filing": f, "detail": c.body,
            "href": doc_link(f, c.document_id),
        })
    return events


def _stage_events(db: Session, case_link: CaseLink, limit: int) -> list[dict]:
    rows = (
        db.query(CaseStageHistory, TaxFiling)
        .join(TaxFiling, TaxFiling.id == CaseStageHistory.case_id)
        .order_by(CaseStageHistory.created_at.desc())
        .limit(limit)
        .all()
    )
    labels = stage_labels(db)
    names = account_names(db, {h.changed_by_account_id for h, _ in rows})
    events = []
    for h, f in rows:
        to = labels.get(h.to_stage_code, h.to_stage_code)
        frm = labels.get(h.from_stage_code, h.from_stage_code) if h.from_stage_code else None
        by = names.get(h.changed_by_account_id, {}).get("name") or "System"
        events.append({
            "at": aware(h.created_at), "kind": "stage", "label": "Stage moved",
            "filing": f, "detail": f"{frm} → {to} · by {by}" if frm else f"Moved to {to} · by {by}",
            "href": case_link(f),
        })
    return events


def _with_case_details(db: Session, events: list[dict]) -> list[dict]:
    """Add client / case columns shared by every filing event."""
    filing_events = [e for e in events if e.get("filing") is not None]
    clients = {
        c.id: c for c in db.query(Client).filter(Client.id.in_({e["filing"].client_id for e in filing_events})).all()
    } if filing_events else {}
    case_ids: dict[str, str] = {}
    for e in filing_events:
        f = e["filing"]
        if f.id not in case_ids:
            case_ids[f.id] = f.get_case_id(db)
        client = clients.get(f.client_id)
        e.update({
            "client_id": f.client_id,
            "client_name": f"{f.first_name} {f.last_name}",
            "customer_no": client.client_number if client else None,
            "email": f.email,
            "case_id": case_ids[f.id],
            "filing_id": f.id,
        })
    for e in events:
        e["time"] = e["at"].strftime("%b %d, %Y · %H:%M") if e["at"] else ""
    return events


def _newest(events: list[dict], limit: int) -> list[dict]:
    events.sort(key=lambda e: e["at"] or _EPOCH, reverse=True)
    return events[:limit]


def client_activity(db: Session, case_link: CaseLink, doc_link: DocLink, limit: int = 300) -> list[dict]:
    """Recent client activity: new filings, uploads / re-uploads, and replies on documents."""
    return _with_case_details(db, _newest(_filing_events(db, case_link, doc_link, limit), limit))


def internal_activity(db: Session, case_link: CaseLink, doc_link: DocLink, account_link: Callable[[AuthAccount], str | None],
                      limit: int = 300) -> list[dict]:
    """Client activity plus stage moves and newly created accounts (admin view)."""
    events = _filing_events(db, case_link, doc_link, limit) + _stage_events(db, case_link, limit)
    accounts = db.query(AuthAccount).order_by(AuthAccount.created_at.desc()).limit(limit).all()
    names = account_names(db, {a.id for a in accounts})
    for a in accounts:
        kind = a.account_type.value.lower() if isinstance(a.account_type, AccountType) else str(a.account_type).lower()
        events.append({
            "at": aware(a.created_at), "kind": "account", "label": f"New {kind} account",
            "filing": None, "detail": f"{names.get(a.id, {}).get('name') or a.email} · {a.email}",
            "href": account_link(a), "account_number": a.account_number,
        })
    return _with_case_details(db, _newest(events, limit))
