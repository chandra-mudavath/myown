"""Admin "My Profile": name changes and password changes for the signed-in admin."""
from sqlalchemy.orm import Session

from app.modules.admin.models.admin import Admin
from app.platform.models.auth import AuthAccount
from app.platform.schemas.auth import _NAME_RE, _NAME_STRICT_RE


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


