"""Admin "My Profile": name changes and password changes for the signed-in admin."""
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.security import hash_password, hash_token, verify_password
from app.modules.admin.models.admin import Admin
from app.platform.models.auth import AuthAccount, AuthRefreshToken
from app.platform.models.system import AuditLog
from app.platform.schemas.auth import _NAME_RE, _NAME_STRICT_RE, _validate_password_strength


def current_admin(db: Session, account: AuthAccount) -> Admin | None:
    return db.query(Admin).filter(Admin.account_id == account.id).first()


def update_name(db: Session, admin: Admin, first_name: str, last_name: str | None) -> str | None:
    """Save the admin's name. Returns an error message, or None when saved. Same rules as sign-up."""
    first = (first_name or "").strip()
    last = (last_name or "").strip() or None
    if not first:
        return "First name is required."
    if len(first) > 15 or (last and len(last) > 15):
        return "Names can be at most 15 characters."
    if not _NAME_RE.match(first):
        return "First name must contain only letters and numbers."
    if last and not _NAME_STRICT_RE.match(last):
        return "Last name must contain only letters."
    admin.first_name, admin.last_name = first, last
    db.commit()
    return None


def change_password(db: Session, account: AuthAccount, current: str, new: str, confirm: str,
                    keep_refresh_token: str | None, ip: str | None = None) -> str | None:
    """Change the password after checking the current one. Signs out every other session.

    Returns an error message, or None on success.
    """
    if not verify_password(current or "", account.password_hash):
        return "Your current password is incorrect."
    if new != confirm:
        return "The new passwords do not match."
    if new == current:
        return "Choose a password different from your current one."
    try:
        _validate_password_strength(new or "")
    except ValueError as exc:
        return str(exc)

    now = datetime.now(timezone.utc)
    account.password_hash = hash_password(new)
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
