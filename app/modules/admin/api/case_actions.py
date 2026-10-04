"""Admin actions on a case page: move the stage, accept / reject documents, post notes, open files.
Same rules as the staff portal (platform/services/case_actions.py)."""
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import AdminAccount
from app.platform.models.filing_document import FilingDocument
from app.platform.models.tax_filing import TaxFiling
from app.platform.services import case_actions

router = APIRouter(prefix="/admin/cases", tags=["admin"])


def _back(case_id: str, section: str, err: str | None, saved: str) -> RedirectResponse:
    query = urlencode({"error": err, "area": section} if err else {"saved": saved, "area": section})
    return RedirectResponse(f"/admin/cases/{case_id}?{query}#{section}", status_code=303)


def _document(db: Session, case_id: str, doc_id: str) -> FilingDocument:
    d = db.query(FilingDocument).filter(FilingDocument.id == doc_id, FilingDocument.filing_id == case_id).first()
    if not d:
        raise HTTPException(status_code=404, detail="Document not found.")
    return d


@router.post("/{case_id}/stage")
def case_move_stage(case_id: str, account: AdminAccount, db: Session = Depends(get_db), to: str = Form("")):
    filing = db.get(TaxFiling, case_id)
    if not filing:
        raise HTTPException(status_code=404, detail="Case not found.")
    before = filing.status
    err = case_actions.move_stage(db, filing, to, account.id)
    return _back(case_id, "workflow", err, "Stage updated." if to != before else "The case is already in that stage.")


@router.post("/{case_id}/documents/{doc_id}/review")
def case_review_document(case_id: str, doc_id: str, account: AdminAccount, db: Session = Depends(get_db),
                         action: str = Form(""), reason: str = Form("")):
    d = _document(db, case_id, doc_id)
    if not d.is_latest:
        return _back(case_id, "documents", "Only the latest version of a document can be reviewed.", "")
    err = case_actions.review_document(db, d, action, reason, actor_account_id=account.id,
                                       reviewer_id=account.id, author_type="admin")
    message = {"reason": "Give the client a reason when asking for a re-upload.", "action": "Unknown review action."}.get(err) if err else None
    return _back(case_id, "documents", message,
                 "Document accepted." if action == "accept" else "Document sent back to the client for a re-upload.")


@router.post("/{case_id}/documents/{doc_id}/comments")
def case_comment_document(case_id: str, doc_id: str, account: AdminAccount, db: Session = Depends(get_db), body: str = Form("")):
    d = _document(db, case_id, doc_id)
    if not body.strip():
        return _back(case_id, "documents", "Write a note first.", "")
    case_actions.comment_on_document(db, d, body, author_type="admin", actor_account_id=account.id)
    return _back(case_id, "documents", None, "Note added to the document.")


@router.get("/{case_id}/documents/{doc_id}/file", name="admin_case_document_file")
def case_document_file(case_id: str, doc_id: str, account: AdminAccount, db: Session = Depends(get_db), download: bool = False):
    d = _document(db, case_id, doc_id)
    return FileResponse(path=d.file_path, filename=d.original_filename, media_type=d.mime_type,
                        content_disposition_type="attachment" if download else "inline")
