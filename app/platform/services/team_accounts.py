"""Company accounts made by an admin or HR: staff (a case role) and HR.

Staff sign-up is off, so this is how internal people get a login. Every account starts with a
temporary password that must be changed at first sign-in (deps.require_verified, AUTH-009).

HR is its own account type (AccountType.HR, row in `hr`), never a staff role: an HR person has
no staff row, so nothing that lists or assigns staff can reach them.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.modules.hr.models.hr import HR
from app.modules.staff.models.staff import Staff, StaffRole
from app.modules.staff.models.staff_records import (
    StaffDocument, StaffEmploymentDetails, StaffRoleAssignment, StaffSalaryHistory,
)
from app.platform.models.auth import AccountType, AuthAccount, AuthRefreshToken
from app.platform.models.case_workflow import CaseAssignment
from app.platform.models.referral import ReferralLead
from app.platform.models.system import AuditLog
from app.platform.services.catalog import staff_roles

HR_ROLE = "HR"


class AccountError(ValueError):
    """A message to show the person who filled the form."""


def temp_password(length: int = 12) -> str:
    from app.modules.admin.services.accounts import temp_password as _make  # one generator for every temp password
    return _make(length)


def _clean(value: str | None) -> str:
    return (value or "").strip()


def create(db: Session, actor_id: str, first_name: str, last_name: str, email: str, phone: str | None,
           role_code: str, *, allow_hr: bool = True) -> tuple[AuthAccount, Staff | HR, str]:
    """Make a staff account (case role) or an HR account. Returns (account, staff or HR row, temporary password)."""
    email = _clean(email).lower()
    first, last = _clean(first_name), _clean(last_name) or None
    role_code = _clean(role_code).upper()
    if not first or not email:
        raise AccountError("Enter a first name and an email address.")
    if len(first) > 15 or (last and len(last) > 15):
        raise AccountError("Names can be at most 15 characters.")
    case_roles = {r.code for r in staff_roles(db)}
    if role_code not in case_roles and not (allow_hr and role_code == HR_ROLE):
        raise AccountError("Choose a role.")
    if db.query(AuthAccount.id).filter(AuthAccount.email == email).first():
        raise AccountError("An account with this email already exists.")

    password = temp_password()
    is_hr = role_code == HR_ROLE
    account = AuthAccount(email=email, password_hash=hash_password(password),
                          account_type=AccountType.HR if is_hr else AccountType.STAFF,
                          is_active=True, is_verified=True, must_change_password=True)
    db.add(account)
    db.flush()
    phone = _clean(phone) or None
    if is_hr:
        person: Staff | HR = HR(account_id=account.id, first_name=first, last_name=last, phone=phone)
    else:
        person = Staff(account_id=account.id, first_name=first, last_name=last, phone=phone, role=StaffRole(role_code))
    db.add(person)
    db.flush()
    db.add(AuditLog(actor_account_id=actor_id, action="account_created", entity_type="auth_account", entity_id=account.id,
                    old_values=None, new_values={"account_type": account.account_type.value, "role": role_code}))
    db.commit()
    return account, person, password


def number_of(person: Staff | HR) -> str:
    return person.hr_number if isinstance(person, HR) else person.staff_number


def _referenced(db: Session, staff: Staff) -> bool:
    """Anything still pointing at the staff row (it is then kept, unused, rather than lose that link)."""
    checks = (
        db.query(CaseAssignment.id).filter(CaseAssignment.staff_id == staff.id),
        db.query(StaffRoleAssignment.id).filter(StaffRoleAssignment.staff_id == staff.id),
        db.query(StaffEmploymentDetails.id).filter((StaffEmploymentDetails.staff_id == staff.id)
                                                   | (StaffEmploymentDetails.reporting_manager_staff_id == staff.id)),
        db.query(StaffDocument.id).filter(StaffDocument.staff_id == staff.id),
        db.query(StaffSalaryHistory.id).filter(StaffSalaryHistory.staff_id == staff.id),
        db.query(ReferralLead.id).filter(ReferralLead.assigned_staff_id == staff.id),
    )
    return any(q.first() for q in checks)


def move_staff_to_hr(db: Session, actor_id: str, staff: Staff) -> HR:
    """Turn a staff member into an HR account. Refused while they are on a case team."""
    open_cases = db.query(CaseAssignment).filter(CaseAssignment.staff_id == staff.id, CaseAssignment.status == "active").count()
    if open_cases:
        raise AccountError(f"Remove {staff.first_name} from {open_cases} case{'s' if open_cases != 1 else ''} first. "
                           "HR has no access to cases.")
    account = staff.account
    hr = db.query(HR).filter(HR.account_id == account.id).first() or HR(
        account_id=account.id, first_name=staff.first_name, last_name=staff.last_name, phone=staff.phone,
        profile_picture=staff.profile_picture)
    db.add(hr)
    old_role = staff.role.value if staff.role else None
    account.account_type = AccountType.HR
    # The old staff session would open the staff portal: sign them in again as HR
    db.query(AuthRefreshToken).filter(AuthRefreshToken.account_id == account.id, AuthRefreshToken.revoked_at.is_(None)
                                      ).update({"revoked_at": datetime.now(timezone.utc)})
    db.flush()
    if not _referenced(db, staff):
        db.delete(staff)
    db.add(AuditLog(actor_account_id=actor_id, action="staff_moved_to_hr", entity_type="auth_account", entity_id=account.id,
                    old_values={"account_type": "STAFF", "role": old_role}, new_values={"account_type": "HR"}))
    db.commit()
    return hr
