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
from app.platform.models.system import AuditLog
from app.platform.schemas.auth import RegisterRequest, validate_password_strength

# Internal staff domain
_INTERNAL_DOMAIN = "covrenttax.com"
# Role addresses: the company's shared admin@ / hr@covrenttax.com, or a prefix (admin.subhani@covrenttax.com);
# the older suffix form (chandra.n.admin@covrenttax.com) still works for existing accounts.
_ADMIN_LOCALS = ("admin",)
_ADMIN_PREFIXES = ("admin.",)
_ADMIN_SUFFIXES = (".admin", ".info")
_HR_LOCAL = "hr"
_HR_PREFIX = "hr."
_HR_SUFFIX = ".hr"


def _is_admin_local(local_part: str) -> bool:
    return (local_part in _ADMIN_LOCALS or local_part.startswith(_ADMIN_PREFIXES)
            or local_part.endswith(_ADMIN_SUFFIXES))


def _is_hr_local(local_part: str) -> bool:
    return local_part == _HR_LOCAL or local_part.startswith(_HR_PREFIX) or local_part.endswith(_HR_SUFFIX)

# ── Constants ────────────────────────────────────────────────────────────────

# Wrong passwords: 5 from one IP address for one email blocks only that IP for that email, so someone
# guessing can't lock the real person out from their own network. The whole account locks only after
# ACCOUNT_LOCK_ATTEMPTS wrong passwords from anywhere (a guess spread over many IPs). Both end after
# LOCKOUT_MINUTES, on a password reset, or when an admin presses Unlock.
MAX_FAILED_ATTEMPTS = 5
ACCOUNT_LOCK_ATTEMPTS = 25
LOCKOUT_MINUTES = 15
_GUESS_REASONS = ("invalid_password", "account_not_found")
PASSWORD_RESET_EXPIRE_MINUTES = 15
# Rate limits (kept in the database until Redis is in place)
IP_FAILED_LOGIN_LIMIT = 20       # wrong sign-ins from one IP address per LOCKOUT_MINUTES
REGISTRATIONS_PER_IP_PER_HOUR = 5
PASSWORD_RESET_EMAILS_PER_HOUR = 3
EMAIL_VERIFY_EXPIRE_HOURS = 24


# ── Lookup ────────────────────────────────────────────────────────────────────

def get_account_by_email(db: Session, email: str) -> Optional[AuthAccount]:
    return db.query(AuthAccount).filter(AuthAccount.email == email.lower().strip()).first()


# ── Registration ──────────────────────────────────────────────────────────────

def register_by_email_domain(db: Session, data: RegisterRequest) -> tuple[AuthAccount, object]:
    """
    Inspect the email domain and role prefix/suffix and create the correct account type:
      - Non-@covrenttax.com  → Client  (is_verified=True, no email check needed)
      - admin@covrenttax.com, admin. prefix (or .admin/.info suffix) → Admin  (is_verified=False, requires email verification)
      - hr@covrenttax.com, hr. prefix (or .hr suffix)             → Staff[HR]  (is_verified=False)
      - @covrenttax.com (plain)                                   → Staff[INITIATOR]  (is_verified=False)
    Returns (account, profile_record).
    """
    email_lower = data.email.lower().strip()
    local_part, domain = (email_lower.rsplit("@", 1) + [""])[:2]

    if domain != _INTERNAL_DOMAIN:
        return _register_client(db, data, email_lower)

    # Internal @covrenttax.com domain
    if _is_admin_local(local_part):
        return _register_admin(db, data, email_lower)

    if _is_hr_local(local_part):
        return _register_staff(db, data, email_lower, StaffRole.HR)

    # Plain staff — default role: INITIATOR
    return _register_staff(db, data, email_lower, StaffRole.INITIATOR)


def is_admin_email(email: str) -> bool:
    """An address register_by_email_domain would turn into an Admin account (admin@, admin. prefix or .admin / .info suffix)."""
    local_part, domain = (email.lower().strip().rsplit("@", 1) + [""])[:2]
    return domain == _INTERNAL_DOMAIN and _is_admin_local(local_part)


def is_internal_email(email: str) -> bool:
    """A company address (@covrenttax.com). The admin sign-up link only accepts these."""
    return (email.lower().strip().rsplit("@", 1) + [""])[1] == _INTERNAL_DOMAIN


def register_admin(db: Session, data: RegisterRequest) -> tuple[AuthAccount, Admin]:
    """Create an Admin account from the admin-only sign-up link, whatever the email address."""
    return _register_admin(db, data, data.email.lower().strip())


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
    """Create a Staff account. It can log in once the email verification link is opened."""
    account = AuthAccount(
        email=email,
        password_hash=hash_password(data.password),
        account_type=AccountType.STAFF,
        is_active=True,
        is_verified=False,
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
    """Create an Admin account. It can log in once the email verification link is opened."""
    account = AuthAccount(
        email=email,
        password_hash=hash_password(data.password),
        account_type=AccountType.ADMIN,
        is_active=True,
        is_verified=False,
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

    if ip_address and _guesses_from_ip(db, normalized, ip_address) >= MAX_FAILED_ATTEMPTS:
        _record_attempt(False, "ip_blocked_for_email")
        return None, "account_locked"

    if not account:
        _record_attempt(False, "account_not_found")
        return None, "invalid_credentials"

    if not account.is_active:
        _record_attempt(False, "account_inactive")
        return None, "account_inactive"

    locked_until = account.locked_until
    if locked_until is not None and locked_until.tzinfo is None:  # MySQL hands back naive UTC
        locked_until = locked_until.replace(tzinfo=timezone.utc)
    if locked_until and locked_until > now:
        _record_attempt(False, "account_locked")
        return None, "account_locked"

    if not verify_password(password, account.password_hash):
        account.failed_login_count += 1
        if account.failed_login_count >= ACCOUNT_LOCK_ATTEMPTS:
            account.locked_until = now + timedelta(minutes=LOCKOUT_MINUTES)
        _record_attempt(False, "invalid_password")
        db.commit()
        return None, "invalid_credentials"

    # Success
    clear_failed_attempts(db, account)
    account.last_login_at = now
    _record_attempt(True)
    return account, None


def _guesses_from_ip(db: Session, email: str, ip_address: str) -> int:
    since = datetime.now(timezone.utc) - timedelta(minutes=LOCKOUT_MINUTES)
    return db.query(AuthLoginAttempt).filter(
        AuthLoginAttempt.email == email, AuthLoginAttempt.ip_address == ip_address,
        AuthLoginAttempt.failure_reason.in_(_GUESS_REASONS), AuthLoginAttempt.created_at >= since,
    ).count()


def clear_failed_attempts(db: Session, account: AuthAccount) -> None:
    """Unlock: forget earlier wrong passwords (the rows stay, marked "_cleared", for the audit trail)."""
    account.failed_login_count, account.locked_until = 0, None
    for reason in _GUESS_REASONS:
        db.query(AuthLoginAttempt).filter(AuthLoginAttempt.email == account.email, AuthLoginAttempt.failure_reason == reason) \
            .update({"failure_reason": reason + "_cleared"}, synchronize_session=False)


def registrations_blocked(db: Session, ip_address: str | None) -> bool:
    """More than REGISTRATIONS_PER_IP_PER_HOUR new accounts from one IP address this hour."""
    if not ip_address:
        return False
    since = datetime.now(timezone.utc) - timedelta(hours=1)
    made = db.query(AuditLog).filter(AuditLog.action == "account_registered", AuditLog.ip_address == ip_address,
                                     AuditLog.created_at >= since).count()
    return made >= REGISTRATIONS_PER_IP_PER_HOUR


def record_registration(db: Session, account: AuthAccount, ip_address: str | None) -> None:
    db.add(AuditLog(actor_account_id=account.id, action="account_registered", entity_type="auth_account",
                    entity_id=account.id, ip_address=ip_address))
    db.commit()


def ip_blocked(db: Session, ip_address: str | None) -> bool:
    """Too many wrong sign-ins from this IP address lately (password guessing across accounts)."""
    if not ip_address:
        return False
    since = datetime.now(timezone.utc) - timedelta(minutes=LOCKOUT_MINUTES)
    failed = db.query(AuthLoginAttempt).filter(
        AuthLoginAttempt.ip_address == ip_address,
        AuthLoginAttempt.success.is_(False),
        AuthLoginAttempt.created_at >= since,
    ).count()
    return failed >= IP_FAILED_LOGIN_LIMIT


def can_send_password_reset(db: Session, account: AuthAccount) -> bool:
    """At most PASSWORD_RESET_EMAILS_PER_HOUR reset emails per account, so the form can't flood someone's inbox."""
    since = datetime.now(timezone.utc) - timedelta(hours=1)
    sent = db.query(AuthPasswordResetToken).filter(
        AuthPasswordResetToken.account_id == account.id, AuthPasswordResetToken.created_at >= since
    ).count()
    return sent < PASSWORD_RESET_EMAILS_PER_HOUR


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


def change_password(db: Session, account: AuthAccount, current: str, new: str, confirm: str,
                    keep_refresh_token: str | None, ip: str | None = None) -> str | None:
    """Change the password after checking the current one (or the admin's temporary one). Signs out every other session.

    Returns an error message, or None on success.
    """
    if not verify_password(current or "", account.password_hash):
        return "Your current password is incorrect."
    if new != confirm:
        return "The new passwords do not match."
    if new == current:
        return "Choose a password different from your current one."
    try:
        validate_password_strength(new or "")
    except ValueError as exc:
        return str(exc)

    now = datetime.now(timezone.utc)
    account.password_hash = hash_password(new)
    account.must_change_password = False
    # Revoke refresh tokens of every other signed-in device; this browser stays signed in.
    others = db.query(AuthRefreshToken).filter(
        AuthRefreshToken.account_id == account.id,
        AuthRefreshToken.revoked_at.is_(None),
    )
    if keep_refresh_token:
        others = others.filter(AuthRefreshToken.token_hash != hash_token(keep_refresh_token))
    others.update({"revoked_at": now}, synchronize_session=False)
    db.add(AuditLog(actor_account_id=account.id, action="password_changed", entity_type="auth_account",
                    entity_id=account.id, ip_address=ip))
    db.commit()
    return None


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
    clear_failed_attempts(db, account)  # the mailbox owner proved who they are
    account.must_change_password = False  # they chose this password themselves
    record.used_at = now

    # Revoke all refresh tokens for security
    db.query(AuthRefreshToken).filter(
        AuthRefreshToken.account_id == account.id,
        AuthRefreshToken.revoked_at.is_(None),
    ).update({"revoked_at": now})

    db.commit()
    return True
