"""Emailed one-time codes: the second step of staff/admin sign-in, and the email check at their sign-up.

The browser keeps a random challenge in a cookie; the database keeps only hashes of the challenge and
of the 6-digit code. A code expires after OTP_EXPIRE_MINUTES and dies after OTP_MAX_ATTEMPTS wrong tries.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.core.security import generate_secure_token, hash_token
from app.platform.models.auth import AuthAccount, AuthLoginOtp

OTP_EXPIRE_MINUTES = 10
OTP_MAX_ATTEMPTS = 5
OTP_RESEND_SECONDS = 60
OTP_MAX_SENDS = 5
OTP_MAX_STARTS_PER_HOUR = 10  # new codes (sign-ins) per account per hour; stops guessing codes over many sign-ins


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)  # SQLite returns naive datetimes


def _code_hash(challenge: str, code: str) -> str:
    return hash_token(f"{challenge}:{code}")


def _new_code() -> str:
    return f"{secrets.randbelow(10**6):06d}"


def start(db: Session, account: AuthAccount, purpose: str) -> tuple[str, str] | None:
    """New code for the account; earlier unused codes stop working. Returns (raw challenge, code),
    or None when the account already asked for OTP_MAX_STARTS_PER_HOUR codes this hour."""
    now = _now()
    recent = db.query(AuthLoginOtp).filter(AuthLoginOtp.account_id == account.id,
                                           AuthLoginOtp.created_at >= now - timedelta(hours=1)).count()
    if recent >= OTP_MAX_STARTS_PER_HOUR:
        return None
    db.query(AuthLoginOtp).filter(AuthLoginOtp.account_id == account.id, AuthLoginOtp.used_at.is_(None)) \
        .update({"used_at": now})
    challenge, code = generate_secure_token(), _new_code()
    db.add(AuthLoginOtp(account_id=account.id, challenge_hash=hash_token(challenge), code_hash=_code_hash(challenge, code),
                        purpose=purpose, expires_at=now + timedelta(minutes=OTP_EXPIRE_MINUTES), last_sent_at=now))
    db.commit()
    return challenge, code


def pending(db: Session, challenge: str | None) -> AuthLoginOtp | None:
    """The live code behind this browser's challenge, if any."""
    if not challenge:
        return None
    otp = db.query(AuthLoginOtp).filter(AuthLoginOtp.challenge_hash == hash_token(challenge)).first()
    if not otp or otp.used_at or _aware(otp.expires_at) <= _now() or otp.attempts >= OTP_MAX_ATTEMPTS:
        return None
    return otp


def verify(db: Session, challenge: str | None, code: str) -> tuple[AuthLoginOtp | None, str | None]:
    """Check a typed code. Returns (otp, None) on success, else (None, "expired" | "locked" | "wrong")."""
    otp = pending(db, challenge)
    if not otp:
        return None, "expired"
    if not secrets.compare_digest(otp.code_hash, _code_hash(challenge, code.strip())):
        otp.attempts += 1
        db.commit()
        return None, "locked" if otp.attempts >= OTP_MAX_ATTEMPTS else "wrong"
    now = _now()
    otp.used_at = now
    account = db.get(AuthAccount, otp.account_id)
    if not account.is_verified:  # typing the code proves they read that mailbox
        account.is_verified, account.email_verified_at = True, now
    db.commit()
    return otp, None


def resend(db: Session, challenge: str | None) -> tuple[AuthLoginOtp | None, str | None, str | None]:
    """A fresh code on the same challenge. Returns (otp, code, None) or (None, None, "expired" | "wait" | "limit")."""
    otp = pending(db, challenge)
    if not otp:
        return None, None, "expired"
    now = _now()
    if (now - _aware(otp.last_sent_at)).total_seconds() < OTP_RESEND_SECONDS:
        return None, None, "wait"
    if otp.sends >= OTP_MAX_SENDS:
        return None, None, "limit"
    code = _new_code()
    otp.code_hash, otp.attempts, otp.sends = _code_hash(challenge, code), 0, otp.sends + 1
    otp.last_sent_at, otp.expires_at = now, now + timedelta(minutes=OTP_EXPIRE_MINUTES)
    db.commit()
    return otp, code, None


def mask_email(email: str) -> str:
    """ravi.kumar@covrenttax.com -> r********r@covrenttax.com"""
    local, _, domain = email.partition("@")
    hidden = local[0] + "*" * max(len(local) - 2, 1) + (local[-1] if len(local) > 1 else "")
    return f"{hidden}@{domain}"
