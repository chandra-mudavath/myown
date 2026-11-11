"""HR documents on file for each employee (ID proof, contract, certificates, ...): uploads and downloads.

Files are checked and stored like case documents (same size and type rules, encrypted at rest by
the storage provider) but in private_storage, outside the public /storage mount. Each download is
written to the access log. Files are never deleted: a renewed copy is uploaded alongside.
"""
from __future__ import annotations

import io
import os
from datetime import date, timedelta

from fastapi import Request, UploadFile
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.files import mime_for, send_file
from app.modules.staff.models.staff import Staff
from app.modules.staff.models.staff_records import StaffDocument
from app.platform.models.lookups import DocumentType
from app.platform.models.system import AuditLog
from app.platform.services import access_log
from app.platform.services.storage_provider import storage_provider
from app.platform.services.storage_service import generate_uuid, read_upload, sanitize_filename

STAFF_DOCUMENTS_DIR = "private_storage/staff_documents"
EXPIRY_WINDOW_DAYS = 30


def document_types(db: Session) -> list[DocumentType]:
    return (db.query(DocumentType).filter(DocumentType.applies_to == "STAFF", DocumentType.is_active.is_(True))
            .order_by(DocumentType.sort_order, DocumentType.name).all())


def upload(db: Session, actor_id: str, staff: Staff, file: UploadFile | None, type_code: str, expiry: str, notes: str) -> str | None:
    if not file or not file.filename:
        return "Choose a file to upload."
    doc_type = db.query(DocumentType).filter(DocumentType.code == type_code, DocumentType.applies_to == "STAFF").first()
    if not doc_type:
        return "Choose the kind of document."
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in settings.ALLOWED_EXTENSIONS:
        return f"This file type isn't allowed. Use {', '.join(settings.ALLOWED_EXTENSIONS)}."
    try:
        expiry_date = date.fromisoformat(expiry.strip()) if (expiry or "").strip() else None
    except ValueError:
        return "Enter the expiry date as a date, or leave it empty."
    data = read_upload(file)
    if not data:
        return "That file is empty."
    stored = f"{doc_type.code.lower()}_{generate_uuid()[:8]}{ext}"
    path = storage_provider.save(io.BytesIO(data), f"{STAFF_DOCUMENTS_DIR}/{staff.id}/{stored}", content_type=mime_for(stored))
    doc = StaffDocument(staff_id=staff.id, document_type_id=doc_type.id, original_filename=sanitize_filename(file.filename),
                        stored_filename=stored, file_path=path, mime_type=mime_for(stored), file_size=len(data),
                        uploaded_by_account_id=actor_id, expiry_date=expiry_date,
                        notes=" ".join((notes or "").split())[:500] or None)
    db.add(doc)
    db.flush()
    db.add(AuditLog(actor_account_id=actor_id, action="staff_document_uploaded", entity_type="staff_document", entity_id=doc.id,
                    old_values=None, new_values={"staff_id": staff.id, "type": doc_type.code}))
    db.commit()
    return None


def download(db: Session, request: Request, account_id: str, doc: StaffDocument, inline: bool = False):
    access_log.record(db, request, account_id, "staff_document_viewed", "staff_document", doc.id, staff_id=doc.staff_id)
    return send_file(doc.file_path, doc.original_filename, inline=inline)


def expiring(db: Session, days: int = EXPIRY_WINDOW_DAYS) -> list[StaffDocument]:
    return (db.query(StaffDocument).filter(StaffDocument.expiry_date.isnot(None),
                                           StaffDocument.expiry_date <= date.today() + timedelta(days=days))
            .order_by(StaffDocument.expiry_date).all())
