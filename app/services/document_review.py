"""Shared helpers for document uploads, uploader names and staff ↔ client comment threads."""
from __future__ import annotations

from fastapi import HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.admin import Admin
from app.models.client import Client
from app.models.filing_document import DocumentComment, FilingDocument
from app.models.staff import Staff
from app.models.tax_filing import TaxFiling
from app.services.storage_service import generate_uuid, save_document_file


def _full_name(person) -> str:
    return " ".join(p for p in (person.first_name, person.last_name) if p) or "Unknown"


def account_names(db: Session, account_ids: set[str]) -> dict[str, dict]:
    """Map login account ids to {"name", "role"} across clients, staff and admins."""
    ids = {a for a in account_ids if a}
    if not ids:
        return {}
    names: dict[str, dict] = {}
    for model, role in ((Client, "client"), (Staff, "staff"), (Admin, "admin")):
        for person in db.query(model).filter(model.account_id.in_(ids)).all():
            names[person.account_id] = {"name": _full_name(person), "role": role}
    return names


def author_for(db: Session, account_id: str, role: str) -> str:
    """Display name to snapshot on a comment written by this account."""
    return account_names(db, {account_id}).get(account_id, {}).get("name") or role.capitalize()


def comment_threads(db: Session, filing_id: str) -> dict[str, list[DocumentComment]]:
    """All comments on a filing's documents, grouped by document group, oldest first."""
    threads: dict[str, list[DocumentComment]] = {}
    rows = (
        db.query(DocumentComment)
        .filter(DocumentComment.filing_id == filing_id)
        .order_by(DocumentComment.created_at)
        .all()
    )
    for c in rows:
        threads.setdefault(c.document_group_id, []).append(c)
    return threads


def add_comment(
    db: Session,
    *,
    document: FilingDocument,
    kind: str,
    body: str,
    author_type: str,
    author_account_id: str,
) -> DocumentComment:
    comment = DocumentComment(
        filing_id=document.filing_id,
        document_group_id=document.document_group_id,
        document_id=document.id,
        kind=kind,
        body=body.strip()[:2000],
        author_type=author_type,
        author_account_id=author_account_id,
        author_name=author_for(db, author_account_id, author_type)[:80],
    )
    db.add(comment)
    return comment


def store_upload(
    db: Session,
    *,
    client: Client,
    filing: TaxFiling,
    file: UploadFile,
    category: str,
    document_name: str | None,
    document_group_id: str | None,
    uploaded_by_type: str,
    uploaded_by_id: str,
) -> FilingDocument:
    """Save one uploaded file as a new document, or as the next version of an existing group."""
    ext = f".{file.filename.split('.')[-1].lower()}" if "." in (file.filename or "") else ""
    if ext not in settings.ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File extension {ext} not allowed. Supported: {', '.join(settings.ALLOWED_EXTENSIONS)}",
        )

    group_id = document_group_id or generate_uuid()
    version = 1
    if document_group_id:
        existing = db.query(FilingDocument).filter(
            FilingDocument.filing_id == filing.id,
            FilingDocument.document_group_id == document_group_id,
        ).all()
        if existing:
            version = max(d.version for d in existing) + 1
            for d in existing:
                d.is_latest = False
                d.status = "superseded"

    stored_name, relative_path, mime_type, file_size = save_document_file(
        file=file,
        client_id=client.id,
        tax_year=filing.tax_year,
        filing_id=filing.id,
        category=category,
        version=version,
        document_name=document_name or file.filename,
    )
    doc = FilingDocument(
        filing_id=filing.id,
        document_group_id=group_id,
        category=category,
        document_name=document_name or file.filename,
        original_filename=file.filename,
        stored_filename=stored_name,
        file_path=relative_path,
        file_size=file_size,
        mime_type=mime_type,
        version=version,
        is_latest=True,
        status="pending_review",
        uploaded_by_type=uploaded_by_type,
        uploaded_by_id=uploaded_by_id,
    )
    db.add(doc)
    return doc
