"""Referrals: clients refer colleagues (a form on Refer & Earn, or their sign-up link), staff and admins
add leads too, and the tax team follows each one up by phone. Shared by the client, staff and admin portals.

Every function that changes data commits and returns an error message, or None.
"""
import re
from datetime import date, datetime, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.modules.client.models.client import Client
from app.modules.staff.models.staff import Staff
from app.platform.models.auth import AuthAccount
from app.platform.models.referral import ReferralFollowUp, ReferralLead
from app.platform.models.system import AuditLog
from app.platform.models.tax_filing import TaxFiling
from app.platform.services.document_review import author_for

# (code, label, pill tone). Order is the pipeline order.
STATUSES = [
    ("new", "New", "blue"),
    ("contacted", "Contacted", "amber"),
    ("follow_up", "Call back", "amber"),
    ("interested", "Interested", "teal"),
    ("not_interested", "Not interested", "slate"),
    ("signed_up", "Signed up", "green"),
    ("filed", "Filed with us", "green"),
]
STATUS_LABEL = {c: label for c, label, _ in STATUSES}
STATUS_TONE = {c: tone for c, _, tone in STATUSES}
# Statuses staff can set by hand; signed_up and filed come from the system.
MANUAL_STATUSES = [s for s in STATUSES if s[0] not in ("signed_up", "filed")]
OPEN_STATUSES = ("new", "contacted", "follow_up", "interested")

CALL_OUTCOMES = [
    ("answered", "Answered"),
    ("no_answer", "No answer"),
    ("busy", "Busy, call back later"),
    ("left_message", "Left a message"),
    ("wrong_number", "Wrong number"),
]
OUTCOME_LABEL = dict(CALL_OUTCOMES)
RELATIONSHIPS = ["Colleague", "Friend", "Family", "Neighbour", "Other"]
# How the referral reached us: (code, label, pill tone)
SOURCES = [("link", "Referral link", "teal"), ("form", "Client form", "blue"), ("staff", "Added by staff", "slate"),
           ("admin", "Added by admin", "slate")]
SOURCE_LABEL = {c: label for c, label, _ in SOURCES}
SOURCE_TONE = {c: tone for c, _, tone in SOURCES}

_PHONE = re.compile(r"^\+?[0-9 ()\-]{7,20}$")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _name(person) -> str:
    return f"{person.first_name} {person.last_name or ''}".strip() if person else ""


def referral_code(client: Client) -> str:
    """The code in a client's referral link (first 8 characters of their id)."""
    return client.id[:8].upper()


def referrer_for_code(db: Session, code: str | None) -> Client | None:
    code = (code or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{8}", code):
        return None
    return db.query(Client).filter(Client.id.like(f"{code}%")).first()


# ── creating leads ────────────────────────────────────────────────────────────

def _clean(name: str, phone: str, email: str, relationship: str, note: str) -> tuple[dict, str | None]:
    data = {
        "name": " ".join((name or "").split())[:120],
        "phone": (phone or "").strip()[:30] or None,
        "email": (email or "").strip().lower()[:255] or None,
        "relationship": (relationship or "").strip()[:60] or None,
        "note": (note or "").strip()[:2000] or None,
    }
    # Full name and phone number are required (client feedback, Oct 2026); email is optional
    if len(data["name"]) < 2:
        return data, "Enter your colleague's full name."
    if not data["phone"]:
        return data, "Enter your colleague's phone number so we can call them."
    if data["phone"] and not _PHONE.match(data["phone"]):
        return data, "Enter a valid phone number, e.g. +1 415 555 0142."
    if data["email"] and not _EMAIL.match(data["email"]):
        return data, "Enter a valid email address."
    return data, None


def add_lead(db: Session, *, referrer: Client | None, name: str, phone: str, email: str, relationship: str, note: str,
             source: str, actor_account_id: str) -> str | None:
    """A client referring a colleague, or staff / admin adding one on a client's behalf."""
    data, err = _clean(name, phone, email, relationship, note)
    if err:
        return err
    dupes = db.query(ReferralLead).filter(ReferralLead.referrer_client_id == (referrer.id if referrer else None))
    if data["phone"] and dupes.filter(ReferralLead.phone == data["phone"]).first():
        return f"{data['name']} is already on the referral list with that phone number."
    if data["email"] and dupes.filter(func.lower(ReferralLead.email) == data["email"]).first():
        return f"{data['name']} is already on the referral list with that email."
    lead = ReferralLead(referrer_client_id=referrer.id if referrer else None, source=source, status="new",
                        created_by_account_id=actor_account_id, **data)
    db.add(lead)
    db.flush()
    db.add(AuditLog(actor_account_id=actor_account_id, action="referral_added", entity_type="referral_lead", entity_id=lead.id,
                    new_values={"name": lead.name, "source": source, "referrer": referrer.client_number if referrer else None}))
    db.commit()
    return None


def record_signup(db: Session, new_client: Client, email: str, code: str | None) -> None:
    """A new client registered with someone's referral link: link them, and mark (or create) the lead."""
    referrer = referrer_for_code(db, code)
    if not referrer or referrer.id == new_client.id:
        return
    new_client.referred_by_client_id = referrer.id
    email = (email or "").strip().lower()
    lead = (db.query(ReferralLead)
            .filter(ReferralLead.referrer_client_id == referrer.id, func.lower(ReferralLead.email) == email,
                    ReferralLead.signed_up_client_id.is_(None))
            .first()) if email else None
    if not lead:
        lead = ReferralLead(referrer_client_id=referrer.id, name=_name(new_client), phone=new_client.phone or None,
                            email=email or None, source="link", created_by_account_id=new_client.account_id)
        db.add(lead)
    lead.status = "signed_up"
    lead.signed_up_client_id = new_client.id
    db.flush()
    db.add(ReferralFollowUp(lead_id=lead.id, kind="status", note="Signed up with the referral link.",
                            author_account_id=new_client.account_id, author_name=_name(new_client) or "Client"))
    db.commit()


# ── reading ───────────────────────────────────────────────────────────────────

def _sync_filed(db: Session, leads: list[ReferralLead]) -> None:
    """A referral who signed up and then filed shows as Filed with us."""
    signed = {l.signed_up_client_id: l for l in leads if l.signed_up_client_id and l.status == "signed_up"}
    if not signed:
        return
    filed = {c for (c,) in db.query(TaxFiling.client_id).filter(TaxFiling.client_id.in_(signed)).distinct()}
    for client_id in filed:
        signed[client_id].status = "filed"
    if filed:
        db.commit()


def _rows(db: Session, leads: list[ReferralLead]) -> list[dict]:
    _sync_filed(db, leads)
    client_ids = {l.referrer_client_id for l in leads} | {l.signed_up_client_id for l in leads}
    clients = {c.id: c for c in db.query(Client).filter(Client.id.in_(client_ids - {None}))} if leads else {}
    staff = {s.id: s for s in db.query(Staff).filter(Staff.id.in_({l.assigned_staff_id for l in leads} - {None}))} if leads else {}
    last_calls: dict[str, ReferralFollowUp] = {}
    if leads:
        for f in (db.query(ReferralFollowUp).filter(ReferralFollowUp.lead_id.in_([l.id for l in leads]), ReferralFollowUp.kind == "call")
                  .order_by(ReferralFollowUp.created_at)):
            last_calls[f.lead_id] = f
    today = date.today()
    rows = []
    for i, l in enumerate(leads, 1):
        ref = clients.get(l.referrer_client_id)
        call = last_calls.get(l.id)
        rows.append({
            "sno": i, "lead": l,
            "status_label": STATUS_LABEL.get(l.status, l.status), "tone": STATUS_TONE.get(l.status, "slate"),
            "source_label": SOURCE_LABEL.get(l.source, l.source), "source_tone": SOURCE_TONE.get(l.source, "slate"),
            "referrer": ref, "referrer_name": _name(ref) if ref else "—",
            "signed_up": clients.get(l.signed_up_client_id),
            "staff_name": _name(staff.get(l.assigned_staff_id)) or None,
            "last_call": call, "last_call_label": OUTCOME_LABEL.get(call.outcome, "") if call else "",
            "overdue": bool(l.next_follow_up and l.next_follow_up < today and l.status in OPEN_STATUSES),
            "due_today": bool(l.next_follow_up and l.next_follow_up == today and l.status in OPEN_STATUSES),
            "tel": re.sub(r"[^0-9+]", "", l.phone or ""),
        })
    return rows


def list_leads(db: Session, *, status: str = "", q: str = "", staff_id: str | None = None, due: bool = False,
               source: str = "") -> list[dict]:
    """Referrals to follow up. Someone who signed up and started a filing is a client now, so they drop off
    these lists (client feedback, Oct 2026); the row stays, marked filed, for the referrer's list and credits."""
    _sync_filed(db, db.query(ReferralLead).filter(ReferralLead.status == "signed_up").all())
    query = db.query(ReferralLead).filter(ReferralLead.status != "filed")
    if source in SOURCE_LABEL:
        query = query.filter(ReferralLead.source == source)
    if status == "open":
        query = query.filter(ReferralLead.status.in_(OPEN_STATUSES))
    elif status in STATUS_LABEL:
        query = query.filter(ReferralLead.status == status)
    if staff_id:
        query = query.filter(ReferralLead.assigned_staff_id == staff_id)
    if due:
        query = query.filter(ReferralLead.next_follow_up <= date.today(), ReferralLead.status.in_(OPEN_STATUSES))
    q = (q or "").strip()
    if q:
        term = f"%{q}%"
        referrers = db.query(Client.id).filter((Client.first_name + " " + func.coalesce(Client.last_name, "")).ilike(term)
                                               | Client.client_number.ilike(term))
        query = query.filter(ReferralLead.name.ilike(term) | ReferralLead.phone.ilike(term) | ReferralLead.email.ilike(term)
                             | ReferralLead.referrer_client_id.in_(referrers))
    # Follow-ups that are due first, then the newest
    leads = query.order_by(ReferralLead.next_follow_up.is_(None), ReferralLead.next_follow_up, ReferralLead.created_at.desc()).all()
    return _rows(db, leads)


def counts(db: Session, staff_id: str | None = None) -> dict:
    query = db.query(ReferralLead.status, func.count(ReferralLead.id))
    if staff_id:
        query = query.filter(ReferralLead.assigned_staff_id == staff_id)
    by_status = dict(query.group_by(ReferralLead.status).all())
    due_q = db.query(func.count(ReferralLead.id)).filter(ReferralLead.next_follow_up <= date.today(), ReferralLead.status.in_(OPEN_STATUSES))
    if staff_id:
        due_q = due_q.filter(ReferralLead.assigned_staff_id == staff_id)
    src_q = db.query(ReferralLead.source, func.count(ReferralLead.id)).filter(ReferralLead.status != "filed")
    if staff_id:
        src_q = src_q.filter(ReferralLead.assigned_staff_id == staff_id)
    by_source = dict(src_q.group_by(ReferralLead.source).all())
    return {
        "all": sum(n for s, n in by_status.items() if s != "filed"),  # filed ones are off the lists
        "open": sum(by_status.get(s, 0) for s in OPEN_STATUSES),
        "due": due_q.scalar() or 0,
        "won": by_status.get("signed_up", 0),
        "by_source": by_source,
        **{s: by_status.get(s, 0) for s, _, _ in STATUSES},
    }


def lead_detail(db: Session, lead_id: str) -> dict | None:
    lead = db.get(ReferralLead, lead_id)
    if not lead:
        return None
    row = _rows(db, [lead])[0]
    row["history"] = (db.query(ReferralFollowUp).filter(ReferralFollowUp.lead_id == lead.id)
                      .order_by(ReferralFollowUp.created_at.desc()).all())
    return row


def client_referrals(db: Session, client: Client) -> list[dict]:
    """A client's own referrals, newest first."""
    leads = db.query(ReferralLead).filter(ReferralLead.referrer_client_id == client.id).order_by(ReferralLead.created_at.desc()).all()
    return _rows(db, leads)


def find_client(db: Session, key: str) -> Client | None:
    """Look a referring client up by client number (CLI-…) or email."""
    key = (key or "").strip()
    if not key:
        return None
    client = db.query(Client).filter(func.upper(Client.client_number) == key.upper()).first()
    if client:
        return client
    return (db.query(Client).join(AuthAccount, AuthAccount.id == Client.account_id)
            .filter(func.lower(AuthAccount.email) == key.lower()).first())


# ── follow-up ─────────────────────────────────────────────────────────────────

def log_followup(db: Session, lead: ReferralLead, *, actor_account_id: str, actor_role: str, outcome: str, note: str,
                 status: str, next_follow_up: str) -> str | None:
    """Log a call (or a note when no outcome is picked), and update the lead's status and next follow-up date."""
    outcome = outcome if outcome in OUTCOME_LABEL else ""
    note = (note or "").strip()[:2000]
    if not outcome and not note and not status and not next_follow_up:
        return "Pick the call outcome or write a note."
    due = None
    if next_follow_up:
        try:
            due = date.fromisoformat(next_follow_up)
        except ValueError:
            return "Enter the next follow-up date as a date."
        if due < date.today():
            return "The next follow-up date can't be in the past."
    if status and status not in dict((s, l) for s, l, _ in MANUAL_STATUSES):
        return "Pick a status from the list."
    if outcome or note:
        db.add(ReferralFollowUp(lead_id=lead.id, kind="call" if outcome else "note", outcome=outcome or None, note=note or None,
                                author_account_id=actor_account_id, author_name=author_for(db, actor_account_id, actor_role)[:80]))
    if outcome:
        lead.last_contacted_at = _now()
        if lead.status == "new" and not status:
            lead.status = "contacted"
    if status and lead.status not in ("signed_up", "filed") and status != lead.status:
        db.add(ReferralFollowUp(lead_id=lead.id, kind="status", note=f"Status: {STATUS_LABEL[lead.status]} → {STATUS_LABEL[status]}",
                                author_account_id=actor_account_id, author_name=author_for(db, actor_account_id, actor_role)[:80]))
        lead.status = status
    if due:
        lead.next_follow_up = due
    elif lead.status == "not_interested":
        lead.next_follow_up = None  # nothing left to follow up
    db.commit()
    return None


def assign(db: Session, lead: ReferralLead, staff_id: str, actor_account_id: str) -> str | None:
    staff = db.get(Staff, staff_id) if staff_id else None
    if staff_id and not staff:
        return "Choose a staff member."
    if lead.assigned_staff_id == (staff.id if staff else None):
        return None
    lead.assigned_staff_id = staff.id if staff else None
    db.add(ReferralFollowUp(lead_id=lead.id, kind="assign", note=f"Assigned to {_name(staff)}" if staff else "Unassigned",
                            author_account_id=actor_account_id, author_name=author_for(db, actor_account_id, "admin")[:80]))
    db.add(AuditLog(actor_account_id=actor_account_id, action="referral_assigned", entity_type="referral_lead", entity_id=lead.id,
                    new_values={"staff_id": staff.id if staff else None}))
    db.commit()
    return None


def staff_options(db: Session) -> list[dict]:
    rows = (db.query(Staff).join(AuthAccount, AuthAccount.id == Staff.account_id)
            .filter(AuthAccount.is_active.is_(True)).order_by(Staff.first_name, Staff.last_name).all())
    return [{"id": s.id, "name": _name(s), "role": s.role.value.title() if s.role else ""} for s in rows]
