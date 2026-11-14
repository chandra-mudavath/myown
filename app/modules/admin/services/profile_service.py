"""Admin "My Profile": name changes and password changes for the signed-in admin."""
from sqlalchemy.orm import Session

from app.modules.admin.models.admin import Admin
from app.platform.models.auth import AuthAccount
from app.platform.schemas.auth import clean_name


def current_admin(db: Session, account: AuthAccount) -> Admin | None:
    return db.query(Admin).filter(Admin.account_id == account.id).first()


def update_name(db: Session, admin: Admin, first_name: str, last_name: str | None) -> str | None:
    """Save the admin's name. Returns an error message, or None when saved. Same rules as sign-up."""
    try:  # letters, numbers and spaces, at least one letter (Nov 2026)
        first = clean_name(first_name, "First name", required=True)
        last = clean_name(last_name, "Last name", required=False)
    except ValueError as e:
        return str(e)
    if len(first) > 15 or (last and len(last) > 15):
        return "Names can be at most 15 characters."
    admin.first_name, admin.last_name = first, last
    db.commit()
    return None


