"""The admin-only sign-up link (client feedback, Oct 2026).

Admins can only be created from /auth/admin/signup/<key>. An admin generates the key on the
Settings page; generating a new one makes the old link stop working. No key means no link.
"""
import hmac
import secrets
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.platform.models.system import AppSetting, AuditLog

KEY = "admin_signup_key"
PATH = "/auth/admin/signup/"


def current_key(db: Session) -> str | None:
    row = db.get(AppSetting, KEY)
    return row.value if row and row.value else None


def is_valid(db: Session, key: str | None) -> bool:
    current = current_key(db)
    return bool(key and current and hmac.compare_digest(key, current))


def generate(db: Session, actor_account_id: str) -> str:
    """Make a new key (replacing any old one) and return it."""
    key = secrets.token_urlsafe(24)
    row = db.get(AppSetting, KEY)
    if row:
        row.value, row.updated_by_account_id = key, actor_account_id
    else:
        db.add(AppSetting(key=KEY, value=key, updated_by_account_id=actor_account_id, updated_at=datetime.now(timezone.utc)))
    db.add(AuditLog(actor_account_id=actor_account_id, action="admin_signup_link_generated", entity_type="app_setting",
                    entity_id=KEY, new_values={"rotated": True}))
    db.commit()
    return key
