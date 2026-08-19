"""
FastAPI dependency injection helpers for authentication & authorization.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Cookie, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import decode_token
from app.models.auth import AccountType, AuthAccount


def get_current_account(
    access_token: Annotated[str | None, Cookie()] = None,
    db: Session = Depends(get_db),
) -> AuthAccount:
    """Read JWT from httpOnly cookie; raise 401 if invalid."""
    if not access_token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")

    payload = decode_token(access_token)
    if not payload or payload.get("type") != "access":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token")

    account = db.query(AuthAccount).filter(AuthAccount.email == payload["sub"]).first()
    if not account or not account.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Account not found or inactive")

    return account


def require_verified(account: AuthAccount = Depends(get_current_account)) -> AuthAccount:
    # TODO: Re-enable email verification check when email sending is configured
    # if not account.is_verified:
    #     raise HTTPException(status.HTTP_403_FORBIDDEN, "Email not verified")
    return account


def require_client(account: AuthAccount = Depends(require_verified)) -> AuthAccount:
    if account.account_type != AccountType.CLIENT:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Clients only")
    return account


def require_staff(account: AuthAccount = Depends(require_verified)) -> AuthAccount:
    if account.account_type not in (AccountType.STAFF, AccountType.ADMIN):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Staff only")
    return account


def require_admin(account: AuthAccount = Depends(require_verified)) -> AuthAccount:
    if account.account_type != AccountType.ADMIN:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admins only")
    return account


# Convenience type aliases for route signatures
CurrentAccount = Annotated[AuthAccount, Depends(get_current_account)]
VerifiedAccount = Annotated[AuthAccount, Depends(require_verified)]
ClientAccount = Annotated[AuthAccount, Depends(require_client)]
StaffAccount = Annotated[AuthAccount, Depends(require_staff)]
AdminAccount = Annotated[AuthAccount, Depends(require_admin)]
