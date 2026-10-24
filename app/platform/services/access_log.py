"""Who looked at sensitive client data: staff and admins opening tax documents, payment proofs and the
case page (bank, FBAR and e-file details). One audit_logs row per view, with the IP address."""
from __future__ import annotations

from fastapi import Request
from sqlalchemy.orm import Session

from app.platform.models.system import AuditLog


def record(db: Session, request: Request, account_id: str, action: str, entity_type: str, entity_id: str,
           **details) -> None:
    db.add(AuditLog(actor_account_id=account_id, action=action, entity_type=entity_type, entity_id=entity_id,
                    new_values=details or None, ip_address=request.client.host if request.client else None))
    db.commit()
