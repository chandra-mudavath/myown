"""Admin account controls for clients and staff: activate / deactivate, unlock, reset password,
change a staff member's role, and edit a client's name and phone.

Deactivating blocks sign-in and every page at once (deps.get_current_account checks is_active),
revokes saved sessions, and for staff ends their case assignments so cases don't sit with
someone who can't sign in. Every change is written to audit_logs (never the password).
"""
import secrets
import string
from datetime import datetime, timezone

from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.modules.client.models.client import Client
from app.modules.client.schemas.client import ContactInformationUpdate, PersonalInformationUpdate
from app.modules.staff.models.staff import Staff, StaffRole
from app.platform.models.auth import AccountType, AuthAccount, AuthRefreshToken
from app.platform.models.case_workflow import CaseAssignment
from app.platform.models.system import AuditLog
from app.platform.services import chat_service
from app.platform.services.catalog import staff_roles

_MANAGED = (AccountType.CLIENT, AccountType.STAFF)


def _audit(db: Session, actor_id: str, action: str, entity_type: str, entity_id: str, old: dict | None, new: dict | None) -> None:
    db.add(AuditLog(actor_account_id=actor_id, action=action, entity_type=entity_type, entity_id=entity_id,
                    old_values=old, new_values=new))


def _revoke_sessions(db: Session, account_id: str, now: datetime) -> int:
    return db.query(AuthRefreshToken).filter(AuthRefreshToken.account_id == account_id,
                                             AuthRefreshToken.revoked_at.is_(None)).update({"revoked_at": now})


def _check(actor_id: str, account: AuthAccount | None) -> str | None:
    if not account:
        return "Account not found."
    if account.id == actor_id:
        return "You can't change your own account here; use My Profile."
    if account.account_type not in _MANAGED:
        return "Only client and staff accounts can be managed here."
    return None


def lock_state(account: AuthAccount) -> datetime | None:
    """When the account is locked until (after too many failed sign-ins), or None."""
    until = account.locked_until
    if until is not None and until.tzinfo is None:
        until = until.replace(tzinfo=timezone.utc)
    return until if until and until > datetime.now(timezone.utc) else None


def set_active(db: Session, actor_id: str, account: AuthAccount, active: bool) -> str | None:
    err = _check(actor_id, account)
    if err:
        return err
    if account.is_active == active:
        return f"The account is already {'active' if active else 'inactive'}."
    now = datetime.now(timezone.utc)
    account.is_active = active
    ended_cases = []
    if not active:
        _revoke_sessions(db, account.id, now)
        if account.account_type == AccountType.STAFF:
            staff = db.query(Staff).filter(Staff.account_id == account.id).first()
            if staff:
                for a in db.query(CaseAssignment).filter(CaseAssignment.staff_id == staff.id, CaseAssignment.status == "active"):
                    a.status, a.unassigned_at = "ended", now
                    ended_cases.append(a.case_id)
                    _audit(db, actor_id, "case_unassigned", "case_assignment", a.id, {"status": "active"},
                           {"status": "ended", "reason": "account deactivated"})
    _audit(db, actor_id, "account_activated" if active else "account_deactivated", "auth_account", account.id,
           {"is_active": not active}, {"is_active": active, **({"cases_unassigned": len(ended_cases)} if ended_cases else {})})
    db.flush()  # sessions don't autoflush: the chat sync must see the ended assignments
    for case_id in set(ended_cases):
        chat_service.sync_case_team(db, case_id)
    db.commit()
    return None


def unlock(db: Session, actor_id: str, account: AuthAccount) -> str | None:
    err = _check(actor_id, account)
    if err:
        return err
    if not lock_state(account) and not account.failed_login_count:
        return "The account isn't locked."
    account.locked_until, account.failed_login_count = None, 0
    _audit(db, actor_id, "account_unlocked", "auth_account", account.id, None, None)
    db.commit()
    return None


def _temp_password(length: int = 12) -> str:
    """Meets the sign-up strength rules: upper, lower, digit and special character."""
    pools = [string.ascii_uppercase, string.ascii_lowercase, string.digits, "!@#$%^&*"]
    chars = [secrets.choice(p) for p in pools] + [secrets.choice("".join(pools)) for _ in range(length - len(pools))]
    secrets.SystemRandom().shuffle(chars)
    return "".join(chars)


def reset_password(db: Session, actor_id: str, account: AuthAccount) -> tuple[str | None, str | None]:
    """Set a temporary password, sign out every session and clear any lock. Returns (password, error)."""
    err = _check(actor_id, account)
    if err:
        return None, err
    password = _temp_password()
    now = datetime.now(timezone.utc)
    account.password_hash = hash_password(password)
    account.locked_until, account.failed_login_count = None, 0
    _revoke_sessions(db, account.id, now)
    _audit(db, actor_id, "password_reset_by_admin", "auth_account", account.id, None, None)
    db.commit()
    return password, None


def change_staff_role(db: Session, actor_id: str, staff: Staff, role_code: str) -> str | None:
    roles = {r.code for r in staff_roles(db)}
    if role_code not in roles:
        return "Choose a role (Initiator, Preparer, Reviewer or Manager)."
    old = staff.role.value if staff.role else None
    if old == role_code:
        return None
    staff.role = StaffRole(role_code)
    _audit(db, actor_id, "staff_role_changed", "staff", staff.id, {"role": old}, {"role": role_code})
    db.commit()
    return None


def update_client(db: Session, actor_id: str, client: Client, first_name: str, last_name: str, phone: str) -> str | None:
    """Same rules as the client's own profile page."""
    try:
        personal = PersonalInformationUpdate(first_name=first_name, last_name=last_name or None,
                                             date_of_birth=client.date_of_birth)
        contact = ContactInformationUpdate(phone=phone)
    except ValidationError as exc:
        # Same wording as the client's own profile page (see the validation handler in main.py)
        field = exc.errors()[0]["loc"][-1]
        return {"first_name": "Please enter a first name.", "last_name": "Please enter a valid last name.",
                "phone": "Please enter a valid phone number."}.get(field, "Please check the details you entered.")
    old = {"first_name": client.first_name, "last_name": client.last_name, "phone": client.phone}
    client.first_name, client.last_name, client.phone = personal.first_name, personal.last_name, contact.phone
    new = {"first_name": client.first_name, "last_name": client.last_name, "phone": client.phone}
    if new != old:
        _audit(db, actor_id, "client_updated", "client", client.id, old, new)
        db.commit()
    return None
