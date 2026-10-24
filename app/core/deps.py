"""
FastAPI dependency injection helpers for authentication & authorization.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Cookie, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.errors import AppError
from app.core.security import decode_token
from app.modules.staff.models.staff import Staff, StaffRole
from app.platform.models.auth import AccountType, AuthAccount

# How the 403 screen names the signed-in person ("You're signed in as …")
_WHO = {AccountType.CLIENT: "a client", AccountType.STAFF: "a member of the tax team", AccountType.ADMIN: "an administrator"}


def get_current_account(
    access_token: Annotated[str | None, Cookie()] = None,
    refresh_token: Annotated[str | None, Cookie()] = None,
    db: Session = Depends(get_db),
) -> AuthAccount:
    """Read JWT from httpOnly cookie; raise 401 if invalid."""
    if not access_token:
        # The access cookie is gone but the refresh one is still here: the session ran out
        if refresh_token:
            raise AppError("AUTH-002", "Session expired")
        raise AppError("AUTH-001", "Not authenticated")

    payload = decode_token(access_token)
    if not payload or payload.get("type") != "access":
        raise AppError("AUTH-002", "Invalid token")

    account = db.query(AuthAccount).filter(AuthAccount.email == payload["sub"]).first()
    if not account or not account.is_active:
        raise AppError("AUTH-003", "Account not found or inactive")

    return account


def require_verified(account: AuthAccount = Depends(get_current_account)) -> AuthAccount:
    """Every portal page and API goes through here. (Staff / admin email checks happen at sign-in, by code.)"""
    if account.must_change_password:  # a temporary password from an admin: nothing opens until they change it
        raise AppError("AUTH-009", "Set a new password first")
    return account


def require_client(account: AuthAccount = Depends(require_verified)) -> AuthAccount:
    if account.account_type != AccountType.CLIENT:
        raise AppError("AUTH-004", "Clients only", you=_WHO.get(account.account_type, ""))
    return account


def require_staff(account: AuthAccount = Depends(require_verified)) -> AuthAccount:
    if account.account_type != AccountType.STAFF:
        raise AppError("AUTH-005", "Staff only", you=_WHO.get(account.account_type, ""))
    return account


def is_hr(db: Session, account: AuthAccount) -> bool:
    return db.query(Staff.role).filter(Staff.account_id == account.id).scalar() == StaffRole.HR


def require_case_staff(account: AuthAccount = Depends(require_staff), db: Session = Depends(get_db)) -> AuthAccount:
    """Staff who work on client cases: everyone except HR, who never see clients, cases or documents."""
    if is_hr(db, account):
        raise AppError("AUTH-008", "HR accounts can't open client cases")
    return account


def require_admin(account: AuthAccount = Depends(require_verified)) -> AuthAccount:
    if account.account_type != AccountType.ADMIN:
        raise AppError("AUTH-006", "Admins only", you=_WHO.get(account.account_type, ""))
    return account


# Convenience type aliases for route signatures
CurrentAccount = Annotated[AuthAccount, Depends(get_current_account)]
VerifiedAccount = Annotated[AuthAccount, Depends(require_verified)]
ClientAccount = Annotated[AuthAccount, Depends(require_client)]
StaffAccount = Annotated[AuthAccount, Depends(require_staff)]
CaseStaffAccount = Annotated[AuthAccount, Depends(require_case_staff)]
AdminAccount = Annotated[AuthAccount, Depends(require_admin)]
