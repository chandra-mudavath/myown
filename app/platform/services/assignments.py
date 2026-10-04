"""Who is working each case (case_assignments). Used by the admin and staff portals.

A case has at most one active assignment per role; assigning someone else to a role ends the
previous one (a reassignment). Ended rows stay for history. Every change re-syncs the case chat
team (chat_service.sync_case_team) and is written to audit_logs.
"""
from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.modules.staff.models.staff import Staff
from app.platform.models.auth import AuthAccount
from app.platform.models.case_workflow import CaseAssignment
from app.platform.models.lookups import StaffRoleLookup
from app.platform.models.system import AuditLog
from app.platform.models.tax_filing import TaxFiling
from app.platform.services import chat_service
from app.platform.services.catalog import staff_roles


def _name(s: Staff) -> str:
    return f"{s.first_name} {s.last_name or ''}".strip()


def team(db: Session, case_id: str) -> list[dict]:
    """Active assignments on a case, in role order."""
    rows = (
        db.query(CaseAssignment, Staff, StaffRoleLookup)
        .join(Staff, Staff.id == CaseAssignment.staff_id)
        .join(StaffRoleLookup, StaffRoleLookup.id == CaseAssignment.role_id)
        .filter(CaseAssignment.case_id == case_id, CaseAssignment.status == "active")
        .order_by(StaffRoleLookup.sort_order, CaseAssignment.assigned_at)
        .all()
    )
    return [{"id": a.id, "staff_id": s.id, "name": _name(s), "staff_number": s.staff_number,
             "role": r.name, "role_code": r.code, "since": a.assigned_at} for a, s, r in rows]


def team_names(db: Session, case_ids: list[str]) -> dict[str, list[str]]:
    """{case_id: ["Priya Sharma (Preparer)", ...]} for case tables."""
    if not case_ids:
        return {}
    out: dict[str, list[str]] = defaultdict(list)
    rows = (
        db.query(CaseAssignment.case_id, Staff.first_name, Staff.last_name, StaffRoleLookup.name)
        .join(Staff, Staff.id == CaseAssignment.staff_id)
        .join(StaffRoleLookup, StaffRoleLookup.id == CaseAssignment.role_id)
        .filter(CaseAssignment.case_id.in_(case_ids), CaseAssignment.status == "active")
        .order_by(StaffRoleLookup.sort_order, CaseAssignment.assigned_at)
    )
    for case_id, first, last, role in rows:
        out[case_id].append(f"{first} {last or ''}".strip() + f" ({role})")
    return dict(out)


def team_for_staff(db: Session, staff_id: str) -> list[dict]:
    """A staff member's active assignments: [{"case_id", "role"}]."""
    rows = (db.query(CaseAssignment.case_id, StaffRoleLookup.name)
            .join(StaffRoleLookup, StaffRoleLookup.id == CaseAssignment.role_id)
            .filter(CaseAssignment.staff_id == staff_id, CaseAssignment.status == "active")
            .order_by(StaffRoleLookup.sort_order))
    return [{"case_id": c, "role": r} for c, r in rows]


def case_ids_for(db: Session, staff_id: str) -> list[str]:
    return [c for (c,) in db.query(CaseAssignment.case_id).filter(
        CaseAssignment.staff_id == staff_id, CaseAssignment.status == "active").distinct()]


def unassigned_case_ids(db: Session) -> list[str]:
    assigned = db.query(CaseAssignment.case_id).filter(CaseAssignment.status == "active")
    return [c for (c,) in db.query(TaxFiling.id).filter(TaxFiling.id.notin_(assigned))]


def workload(db: Session) -> dict[str, int]:
    """{staff_id: number of cases they are actively assigned to}."""
    return dict(db.query(CaseAssignment.staff_id, func.count(func.distinct(CaseAssignment.case_id)))
                .filter(CaseAssignment.status == "active").group_by(CaseAssignment.staff_id).all())


def assignable_staff(db: Session) -> list[dict]:
    """Active staff with their own role and current caseload, for the assign form."""
    load = workload(db)
    rows = (db.query(Staff).join(AuthAccount, AuthAccount.id == Staff.account_id)
            .filter(AuthAccount.is_active.is_(True)).order_by(Staff.first_name, Staff.last_name).all())
    return [{"id": s.id, "name": _name(s), "staff_number": s.staff_number,
             "role": s.role.value if s.role else None, "cases": load.get(s.id, 0)} for s in rows]


def _audit(db: Session, actor_id: str, action: str, a: CaseAssignment, old: dict | None, new: dict | None) -> None:
    db.add(AuditLog(actor_account_id=actor_id, action=action, entity_type="case_assignment", entity_id=a.id,
                    old_values=old, new_values=new))


def assign(db: Session, actor_id: str, case_id: str, staff_id: str, role_code: str | None) -> str | None:
    """Put a staff member on a case in a role (their own role when none is given). Returns an error or None."""
    filing = db.get(TaxFiling, case_id)
    if not filing:
        return "Case not found."
    staff = db.get(Staff, staff_id) if staff_id else None
    if not staff:
        return "Choose a staff member."
    account = db.get(AuthAccount, staff.account_id)
    if not account or not account.is_active:
        return f"{_name(staff)}'s account is inactive."
    roles = {r.code: r for r in staff_roles(db)}
    code = role_code or (staff.role.value if staff.role else "")
    role = roles.get(code)
    if not role:
        return "Choose a case role (Initiator, Preparer, Reviewer or Manager)."

    current = (db.query(CaseAssignment)
               .filter(CaseAssignment.case_id == case_id, CaseAssignment.role_id == role.id, CaseAssignment.status == "active")
               .with_for_update().all())
    if any(a.staff_id == staff.id for a in current):
        return f"{_name(staff)} is already the {role.name.lower()} on this case."
    now = datetime.now(timezone.utc)
    previous = []
    for a in current:  # reassignment: the role moves to the new person
        a.status, a.unassigned_at = "ended", now
        previous.append(a.staff_id)
        _audit(db, actor_id, "case_unassigned", a, {"staff_id": a.staff_id, "role": role.code, "status": "active"},
               {"status": "ended", "reason": "reassigned"})
    new = CaseAssignment(case_id=case_id, staff_id=staff.id, role_id=role.id, assigned_by_account_id=actor_id,
                         assigned_at=now, status="active")
    db.add(new)
    db.flush()
    _audit(db, actor_id, "case_assigned", new, {"replaced_staff_ids": previous} if previous else None,
           {"case": filing.case_number, "staff_id": staff.id, "role": role.code})
    chat_service.sync_case_team(db, case_id)
    db.commit()
    return None


def assign_many(db: Session, actor_id: str, case_ids: list[str], staff_id: str, role_code: str | None) -> tuple[int, list[str]]:
    """Assign one staff member to several cases (the Work Queue's bulk assign).

    Each case goes through assign(), so the same rules apply. Returns (number assigned,
    ["FLI_… : reason", ...] for the ones skipped).
    """
    done, skipped = 0, []
    for case_id in dict.fromkeys(case_ids):  # de-duplicate, keep order
        err = assign(db, actor_id, case_id, staff_id, role_code)
        if err:
            db.rollback()
            filing = db.get(TaxFiling, case_id)
            skipped.append(f"{filing.case_number if filing else 'Unknown case'}: {err}")
        else:
            done += 1
    return done, skipped


def unassign(db: Session, actor_id: str, assignment_id: str, case_id: str) -> str | None:
    a = db.get(CaseAssignment, assignment_id)
    if not a or a.case_id != case_id:
        return "Assignment not found."
    if a.status != "active":
        return "That assignment has already ended."
    a.status, a.unassigned_at = "ended", datetime.now(timezone.utc)
    _audit(db, actor_id, "case_unassigned", a, {"staff_id": a.staff_id, "status": "active"}, {"status": "ended"})
    db.flush()  # sessions don't autoflush: the chat sync must see this row as ended
    chat_service.sync_case_team(db, case_id)
    db.commit()
    return None
