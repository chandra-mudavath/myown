"""
Authentication service layer.
Handles business logic for auth operations — keeps routes thin.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.core.security import (
    hash_password,
    verify_password,
    generate_secure_token,
    hash_token,
    create_access_token,
    create_refresh_token,
)
from app.platform.models.auth import (
    AccountType,
    AuthAccount,
    AuthLoginAttempt,
    AuthPasswordResetToken,
    AuthEmailVerificationToken,
    AuthRefreshToken,
)
from app.modules.client.models.client import Client
from app.modules.staff.models.staff import Staff, StaffRole
from app.modules.admin.models.admin import Admin
from app.platform.schemas.auth import RegisterRequest

# Internal staff domain
_INTERNAL_DOMAIN = "urtax.com"
_ADMIN_SUFFIXES = (".admin", ".info")   # e.g. chandra.n.admin@urtax.com
_HR_SUFFIX = ".hr"                       # e.g. chandra.n.hr@urtax.com

# ── Constants ────────────────────────────────────────────────────────────────

MAX_FAILED_ATTEMPTS = 5
LOCKOUT_MINUTES = 15
PASSWORD_RESET_EXPIRE_MINUTES = 15
EMAIL_VERIFY_EXPIRE_HOURS = 24


# ── Lookup ────────────────────────────────────────────────────────────────────

def get_account_by_email(db: Session, email: str) -> Optional[AuthAccount]:
    return db.query(AuthAccount).filter(AuthAccount.email == email.lower().strip()).first()


# ── Registration ──────────────────────────────────────────────────────────────

def register_by_email_domain(db: Session, data: RegisterRequest) -> tuple[AuthAccount, object]:
    """
    Inspect the email domain/suffix and create the correct account type:
      - Non-@urtax.com  → Client  (is_verified=True, no email check needed)
      - @urtax.com + .admin/.info suffix → Admin  (is_verified=False, requires email verification)
      - @urtax.com + .hr suffix           → Staff[HR]  (is_verified=False)
      - @urtax.com (plain)                → Staff[INITIATOR]  (is_verified=False)
    Returns (account, profile_record).
    """
    email_lower = data.email.lower().strip()
    local_part, domain = (email_lower.rsplit("@", 1) + [""])[:2]

    if domain != _INTERNAL_DOMAIN:
        return _register_client(db, data, email_lower)

    # Internal @urtax.com domain
    if any(local_part.endswith(s) for s in _ADMIN_SUFFIXES):
        return _register_admin(db, data, email_lower)

    if local_part.endswith(_HR_SUFFIX):
        return _register_staff(db, data, email_lower, StaffRole.HR)

    # Plain staff — default role: INITIATOR
    return _register_staff(db, data, email_lower, StaffRole.INITIATOR)


def _register_client(db: Session, data: RegisterRequest, email: str) -> tuple[AuthAccount, Client]:
    """Create a Client account. Verified immediately — no email check needed."""
    account = AuthAccount(
        email=email,
        password_hash=hash_password(data.password),
        account_type=AccountType.CLIENT,
        is_active=True,
        is_verified=True,  # Clients can log in immediately
    )
    db.add(account)
    db.flush()

    client = Client(
        account_id=account.id,
        first_name=data.first_name,
        last_name=data.last_name,
        phone=data.phone,
    )
    db.add(client)
    db.commit()
    db.refresh(account)
    db.refresh(client)
    return account, client


def _register_staff(db: Session, data: RegisterRequest, email: str, role: StaffRole) -> tuple[AuthAccount, Staff]:
    """Create a Staff account. Temporarily verified immediately until company email is set up."""
    account = AuthAccount(
        email=email,
        password_hash=hash_password(data.password),
        account_type=AccountType.STAFF,
        is_active=True,
        # is_verified=False,  # TODO: Re-enable when company domain email is configured
        is_verified=True,  # Temporarily allow direct login until company email is set up
    )
    db.add(account)
    db.flush()

    staff = Staff(
        account_id=account.id,
        first_name=data.first_name,
        last_name=data.last_name,
        phone=data.phone,
        role=role,
    )
    db.add(staff)
    db.commit()
    db.refresh(account)
    db.refresh(staff)
    return account, staff


def _register_admin(db: Session, data: RegisterRequest, email: str) -> tuple[AuthAccount, Admin]:
    """Create an Admin account. Temporarily verified immediately until company email is set up."""
    account = AuthAccount(
        email=email,
        password_hash=hash_password(data.password),
        account_type=AccountType.ADMIN,
        is_active=True,
        # is_verified=False,  # TODO: Re-enable when company domain email is configured
        is_verified=True,  # Temporarily allow direct login until company email is set up
    )
    db.add(account)
    db.flush()

    admin = Admin(
        account_id=account.id,
        first_name=data.first_name,
        last_name=data.last_name,
    )
    db.add(admin)
    db.commit()
    db.refresh(account)
    db.refresh(admin)
    return account, admin


def register_client(db: Session, data: RegisterRequest) -> tuple[AuthAccount, Client, str]:
    """
    Legacy function kept for backwards compatibility.
    Create AuthAccount + Client in a single transaction.
    Returns (account, client, raw_email_verification_token).
    """
    account, client = _register_client(db, data, data.email.lower().strip())
    raw_token, token_record = _create_email_verification_token(db, account)
    db.add(token_record)
    db.commit()
    return account, client, raw_token


def _create_email_verification_token(db: Session, account: AuthAccount) -> tuple[str, AuthEmailVerificationToken]:
    raw = generate_secure_token()
    record = AuthEmailVerificationToken(
        account_id=account.id,
        token_hash=hash_token(raw),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=EMAIL_VERIFY_EXPIRE_HOURS),
    )
    return raw, record


# ── Email verification ────────────────────────────────────────────────────────

def verify_email_token(db: Session, raw_token: str) -> Optional[AuthAccount]:
    """Validate token, mark account verified, invalidate token. Returns account or None."""
    hashed = hash_token(raw_token)
    now = datetime.now(timezone.utc)
    record = (
        db.query(AuthEmailVerificationToken)
        .filter(
            AuthEmailVerificationToken.token_hash == hashed,
            AuthEmailVerificationToken.verified_at.is_(None),
            AuthEmailVerificationToken.expires_at > now,
        )
        .first()
    )
    if not record:
        return None

    record.verified_at = now
    account = record.account
    account.is_verified = True
    account.email_verified_at = now
    db.commit()
    return account


def resend_verification(db: Session, account: AuthAccount) -> str:
    raw, record = _create_email_verification_token(db, account)
    db.add(record)
    db.commit()
    return raw


# ── Login ─────────────────────────────────────────────────────────────────────

def authenticate(
    db: Session,
    email: str,
    password: str,
    ip_address: str | None,
    user_agent: str | None,
) -> tuple[Optional[AuthAccount], Optional[str]]:
    """
    Authenticate a user.
    Returns (account, failure_reason).
    If success: (account, None).
    If failure: (None, reason_string).
    """
    normalized = email.lower().strip()
    now = datetime.now(timezone.utc)

    account = get_account_by_email(db, normalized)

    def _record_attempt(success: bool, reason: str | None = None) -> None:
        attempt = AuthLoginAttempt(
            account_id=account.id if account else None,
            email=normalized,
            ip_address=ip_address,
            user_agent=user_agent,
            success=success,
            failure_reason=reason,
        )
        db.add(attempt)
        db.commit()

    if not account:
        _record_attempt(False, "account_not_found")
        return None, "invalid_credentials"

    if not account.is_active:
        _record_attempt(False, "account_inactive")
        return None, "account_inactive"

    if account.locked_until and account.locked_until > now:
        _record_attempt(False, "account_locked")
        return None, "account_locked"

    if not verify_password(password, account.password_hash):
        account.failed_login_count += 1
        if account.failed_login_count >= MAX_FAILED_ATTEMPTS:
            account.locked_until = now + timedelta(minutes=LOCKOUT_MINUTES)
        _record_attempt(False, "invalid_password")
        db.commit()
        return None, "invalid_credentials"

    # Success
    account.failed_login_count = 0
    account.locked_until = None
    account.last_login_at = now
    _record_attempt(True)
    return account, None


# ── Token pair ────────────────────────────────────────────────────────────────

def issue_tokens(db: Session, account: AuthAccount) -> tuple[str, str]:
    """Issue a new JWT access + refresh token pair. Stores hashed refresh token."""
    access = create_access_token(subject=account.email, account_type=account.account_type.value)
    raw_refresh = generate_secure_token(48)

    refresh_record = AuthRefreshToken(
        account_id=account.id,
        token_hash=hash_token(raw_refresh),
        expires_at=datetime.now(timezone.utc) + timedelta(days=7),
    )
    db.add(refresh_record)
    db.commit()

    return access, raw_refresh


def revoke_refresh_token(db: Session, raw_token: str) -> None:
    hashed = hash_token(raw_token)
    record = db.query(AuthRefreshToken).filter(AuthRefreshToken.token_hash == hashed).first()
    if record:
        record.revoked_at = datetime.now(timezone.utc)
        db.commit()


def rotate_refresh_token(db: Session, raw_old_token: str) -> Optional[tuple[str, str]]:
    """Validate old refresh token, revoke it, issue new pair."""
    hashed = hash_token(raw_old_token)
    now = datetime.now(timezone.utc)
    record = (
        db.query(AuthRefreshToken)
        .filter(
            AuthRefreshToken.token_hash == hashed,
            AuthRefreshToken.revoked_at.is_(None),
            AuthRefreshToken.expires_at > now,
        )
        .first()
    )
    if not record:
        return None

    record.revoked_at = now
    db.commit()
    return issue_tokens(db, record.account)


# ── Forgot / Reset password ───────────────────────────────────────────────────

def create_password_reset_token(db: Session, account: AuthAccount) -> str:
    raw = generate_secure_token()
    record = AuthPasswordResetToken(
        account_id=account.id,
        token_hash=hash_token(raw),
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=PASSWORD_RESET_EXPIRE_MINUTES),
    )
    db.add(record)
    db.commit()
    return raw


def reset_password(db: Session, raw_token: str, new_password: str) -> bool:
    """Validate token, update password, mark token used, revoke all refresh tokens. Returns success."""
    hashed = hash_token(raw_token)
    now = datetime.now(timezone.utc)
    record = (
        db.query(AuthPasswordResetToken)
        .filter(
            AuthPasswordResetToken.token_hash == hashed,
            AuthPasswordResetToken.used_at.is_(None),
            AuthPasswordResetToken.expires_at > now,
        )
        .first()
    )
    if not record:
        return False

    account = record.account
    account.password_hash = hash_password(new_password)
    record.used_at = now

    # Revoke all refresh tokens for security
    db.query(AuthRefreshToken).filter(
        AuthRefreshToken.account_id == account.id,
        AuthRefreshToken.revoked_at.is_(None),
    ).update({"revoked_at": now})

    db.commit()
    return True
