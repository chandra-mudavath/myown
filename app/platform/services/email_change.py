"""Changing a client's sign-in email (client feedback, Nov 2026).

The client asks on Email & Mobile (or Personal Info → Contact). The request waits for an admin, who approves or
rejects it on Admin → Email change requests. On approval the account's email, which is the username, becomes the
new address; the password does not change. Until then the client keeps signing in with the current email.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.platform.models.auth import AuthAccount, EmailChangeRequest
from app.platform.models.system import AuditLog

PENDING = "pending"


def _audit(db: Session, actor_id: str | None, action: str, req: EmailChangeRequest, new: dict | None = None) -> None:
    db.add(AuditLog(actor_account_id=actor_id, action=action, entity_type="email_change_request", entity_id=req.id,
                    old_values={"email": req.old_email}, new_values={"email": req.new_email, **(new or {})}))


def _taken(db: Session, email: str, account_id: str) -> bool:
    return db.query(AuthAccount).filter(func.lower(AuthAccount.email) == email, AuthAccount.id != account_id).first() is not None


def pending_for(db: Session, account_id: str) -> EmailChangeRequest | None:
    return (db.query(EmailChangeRequest).filter(EmailChangeRequest.account_id == account_id, EmailChangeRequest.status == PENDING)
            .order_by(EmailChangeRequest.created_at.desc()).first())


def latest_for(db: Session, account_id: str) -> EmailChangeRequest | None:
    return (db.query(EmailChangeRequest).filter(EmailChangeRequest.account_id == account_id)
            .order_by(EmailChangeRequest.created_at.desc()).first())


def request_change(db: Session, account: AuthAccount, new_email: str) -> tuple[str | None, str | None]:
    """(message, error). A new request replaces one still waiting."""
    email = (new_email or "").strip().lower()
    if not email or "@" not in email:
        return None, "Enter a valid email address."
    if email == account.email.lower():
        return "This is already your sign-in email.", None
    if _taken(db, email, account.id):
        return None, "This email address is already associated with another account."
    other = db.query(EmailChangeRequest).filter(func.lower(EmailChangeRequest.new_email) == email,
                                                EmailChangeRequest.status == PENDING,
                                                EmailChangeRequest.account_id != account.id).first()
    if other:
        return None, "This email address is already associated with another account."
    for old in db.query(EmailChangeRequest).filter(EmailChangeRequest.account_id == account.id, EmailChangeRequest.status == PENDING):
        old.status = "cancelled"
    req = EmailChangeRequest(account_id=account.id, old_email=account.email, new_email=email, status=PENDING)
    db.add(req)
    db.flush()
    _audit(db, account.id, "email_change_requested", req)
    db.commit()
    return (f"Request sent. An admin will approve the change to {email}. Until then, keep signing in with "
            f"{account.email}. Your password stays the same."), None


def pending(db: Session) -> list[dict]:
    """Requests waiting for an admin, oldest first, with the client's name."""
    from app.modules.client.models.client import Client  # noqa: PLC0415 (avoid a platform → module import at load)
    rows = (db.query(EmailChangeRequest, Client).outerjoin(Client, Client.account_id == EmailChangeRequest.account_id)
            .filter(EmailChangeRequest.status == PENDING).order_by(EmailChangeRequest.created_at).all())
    return [{"request": r, "client": c, "name": f"{c.first_name} {c.last_name or ''}".strip() if c else r.old_email} for r, c in rows]


def recent(db: Session, limit: int = 20) -> list[EmailChangeRequest]:
    return (db.query(EmailChangeRequest).filter(EmailChangeRequest.status.in_(("approved", "rejected")))
            .order_by(EmailChangeRequest.decided_at.desc()).limit(limit).all())


def approve(db: Session, admin_account_id: str, req: EmailChangeRequest) -> str | None:
    """Make the new address the sign-in email. Returns an error, or None."""
    if req.status != PENDING:
        return "This request has already been handled."
    account = db.get(AuthAccount, req.account_id)
    if not account:
        return "The client's account no longer exists."
    if _taken(db, req.new_email.lower(), account.id):
        return "Another account already uses this email; reject the request instead."
    account.email = req.new_email.lower()
    req.status, req.decided_by_account_id, req.decided_at = "approved", admin_account_id, datetime.now(timezone.utc)
    _audit(db, admin_account_id, "email_change_approved", req)
    db.commit()
    return None


def reject(db: Session, admin_account_id: str, req: EmailChangeRequest, reason: str) -> str | None:
    if req.status != PENDING:
        return "This request has already been handled."
    reason = " ".join((reason or "").split())[:500]
    if not reason:
        return "Give the client a reason, e.g. \"Please call us to confirm this change\"."
    req.status, req.reason = "rejected", reason
    req.decided_by_account_id, req.decided_at = admin_account_id, datetime.now(timezone.utc)
    _audit(db, admin_account_id, "email_change_rejected", req, {"reason": reason})
    db.commit()
    return None
