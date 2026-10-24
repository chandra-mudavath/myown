"""Case workflow actions shared by the staff and admin portals: move a case to another stage,
accept or reject a document, and post a note on a document.

Each function validates, records the change and commits. They return an error message, or None.
"""
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.platform.models.case_workflow import CaseAssignment, CaseStageHistory
from app.platform.models.filing_document import FilingDocument
from app.platform.models.tax_filing import TaxFiling
from app.platform.services import chat_service
from app.platform.services.catalog import case_stages, stage_labels
from app.platform.services.document_review import add_comment


# A new filing waits in this stage until someone is assigned to it (client feedback, Oct 2026).
REGISTERED_STAGE = "pending_review"
NOT_ASSIGNED_ERROR = "Assign a staff member to this case before moving it out of Registered."


def is_assigned(db: Session, case_id: str) -> bool:
    return db.query(CaseAssignment.id).filter(CaseAssignment.case_id == case_id, CaseAssignment.status == "active").first() is not None


def move_stage(db: Session, filing: TaxFiling, to: str, actor_account_id: str, comment: str = "") -> str | None:
    """Move a filing to another workflow stage and record it (with an optional comment) in case_stage_history."""
    if to not in {s.code for s in case_stages(db)}:
        return "Unknown stage."
    if to != filing.status and filing.status == REGISTERED_STAGE and not is_assigned(db, filing.id):
        return NOT_ASSIGNED_ERROR
    if to != filing.status:
        known = stage_labels(db)
        db.add(CaseStageHistory(
            case_id=filing.id,
            from_stage_code=filing.status if filing.status in known else None,
            to_stage_code=to,
            changed_by_account_id=actor_account_id,
            comment=(comment or "").strip()[:1000] or None,
        ))
        filing.status = to
        chat_service.on_case_stage_changed(db, filing)  # start / cancel the 3-month hiding timer
    db.commit()
    return None


def review_document(db: Session, document: FilingDocument, action: str, reason: str, *,
                    actor_account_id: str, reviewer_id: str, author_type: str) -> str | None:
    """Accept a document, or send it back to the client with a reason to re-upload.

    reviewer_id is stored on the document (staff id for staff, account id for admins).
    Returns "reason" when a rejection has no reason, "action" for an unknown action.
    """
    reason = (reason or "").strip()
    if action == "accept":
        document.status = "accepted"
        document.rejection_reason = None
    elif action == "reject":
        if not reason:
            return "reason"
        document.status = "rejected_reupload_requested"
        document.rejection_reason = reason[:1000]
        add_comment(db, document=document, kind="rejection", body=reason, author_type=author_type,
                    author_account_id=actor_account_id)
    else:
        return "action"
    document.reviewed_at = datetime.now(timezone.utc)
    document.reviewed_by_id = reviewer_id
    db.commit()
    return None


def comment_on_document(db: Session, document: FilingDocument, body: str, *, author_type: str, actor_account_id: str) -> None:
    """A note in the document's conversation with the client (empty notes are ignored)."""
    if body.strip():
        add_comment(db, document=document, kind="note", body=body, author_type=author_type, author_account_id=actor_account_id)
        db.commit()
