"""Chat: who may see and post in each conversation, and the work behind every chat action.

Kinds (app/platform/models/chat.py):
- DIRECT  two internal members (account_types.is_internal). One general thread per pair, plus one per filing.
- GROUP   a named group of internal members, optionally about one filing.
- CASE    the client and the case team on one filing; one per filing.
- QUERY   a question a client raised, optionally about one of their filings.

Routes call these functions and never query the chat tables themselves, so every rule in
docs/DATABASE_POLICIES.md ("Messaging") is enforced in one place.

Case team: staff with an active case_assignments row. Nothing assigns cases yet, so until it
does every active staff member is part of each case thread (the same people who see every
case today). sync_case_team() narrows it once assignments are used.
"""
from __future__ import annotations

import io
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import HTTPException, UploadFile, status
from sqlalchemy import and_, func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile as StarletteUploadFile

from app.core.config import settings
from app.modules.admin.models.admin import Admin
from app.platform.models.auth import AuthAccount
from app.platform.models.case_workflow import CaseAssignment
from app.platform.models.chat import ChatAttachment, ChatMessage, ChatParticipant, ChatThread, direct_key_for
from app.modules.client.models.client import Client
from app.platform.models.lookups import AccountTypeLookup, CaseStage, ChatQueryTopic
from app.modules.staff.models.staff import Staff
from app.platform.models.system import AuditLog
from app.platform.models.tax_filing import TaxFiling
from app.platform.services.document_review import account_names
from app.platform.services.storage_provider import storage_provider

BODY_MAX = 4000
PREVIEW_LEN = 160
EDIT_WINDOW = timedelta(minutes=15)
MAX_FILES = 5
GROUP_MIN_OTHERS = 2


# ── basics ────────────────────────────────────────────────────────────────

def now() -> datetime:
    return datetime.now(timezone.utc)


def _utc(dt: datetime | None) -> datetime | None:
    """MySQL hands back naive datetimes; every stored time is UTC."""
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def iso(dt: datetime | None) -> str | None:
    dt = _utc(dt)
    return dt.isoformat() if dt else None


def _hide_delay() -> timedelta:
    return timedelta(days=settings.CHAT_HIDE_AFTER_DAYS)


def _fail(code: int, message: str) -> HTTPException:
    return HTTPException(status_code=code, detail=message)


def _initials(name: str) -> str:
    parts = [p for p in name.split() if p]
    return "".join(p[0] for p in parts[:2]).upper() or "?"


@dataclass
class Viewer:
    """The signed-in account plus what its account type allows."""
    account: AuthAccount
    code: str  # CLIENT, STAFF, ADMIN, HR
    internal: bool
    case_access: bool
    client: Client | None

    @property
    def id(self) -> str:
        return self.account.id

    @property
    def is_admin(self) -> bool:
        return self.code == "ADMIN"

    @property
    def works_cases(self) -> bool:
        """Internal and allowed on cases: staff and admins, not HR."""
        return self.internal and self.case_access


def account_code(account: AuthAccount) -> str:
    return getattr(account.account_type, "value", account.account_type)


def viewer_for(db: Session, account: AuthAccount) -> Viewer:
    code = account_code(account)
    kind = db.query(AccountTypeLookup).filter(AccountTypeLookup.code == code).first()
    client = db.query(Client).filter(Client.account_id == account.id).first() if code == "CLIENT" else None
    return Viewer(
        account=account,
        code=code,
        internal=bool(kind and kind.is_internal),
        case_access=bool(kind and kind.has_case_access),
        client=client,
    )


def _internal_codes(db: Session) -> set[str]:
    return {c for (c,) in db.query(AccountTypeLookup.code).filter(AccountTypeLookup.is_internal.is_(True))}


def _active_internal_account(db: Session, account_id: str) -> AuthAccount | None:
    acc = db.query(AuthAccount).filter(AuthAccount.id == account_id, AuthAccount.is_active.is_(True)).first()
    return acc if acc and account_code(acc) in _internal_codes(db) else None


# ── participants ──────────────────────────────────────────────────────────

def _participant(db: Session, thread_id: str, account_id: str) -> ChatParticipant | None:
    return db.query(ChatParticipant).filter(
        ChatParticipant.thread_id == thread_id, ChatParticipant.account_id == account_id
    ).first()


def _active_participant(db: Session, thread_id: str, account_id: str) -> ChatParticipant | None:
    p = _participant(db, thread_id, account_id)
    return p if p and p.left_at is None else None


def _add_participant(db: Session, thread: ChatThread, account_id: str, reason: str, role: str = "member") -> ChatParticipant:
    """Add someone, or bring them back if they had left."""
    p = _participant(db, thread.id, account_id)
    if p:
        if p.left_at is not None:
            p.left_at = None
            p.joined_at = now()
            p.added_reason = reason
        if role == "owner":
            p.member_role = "owner"
        return p
    p = ChatParticipant(thread_id=thread.id, account_id=account_id, added_reason=reason, member_role=role)
    db.add(p)
    return p


def _active_member_ids(db: Session, thread_id: str) -> list[str]:
    return [a for (a,) in db.query(ChatParticipant.account_id).filter(
        ChatParticipant.thread_id == thread_id, ChatParticipant.left_at.is_(None))]


# ── access rules ──────────────────────────────────────────────────────────

def _filing_owner_account(db: Session, filing: TaxFiling) -> str | None:
    return db.query(Client.account_id).filter(Client.id == filing.client_id).scalar()


def can_view(db: Session, v: Viewer, t: ChatThread) -> bool:
    if t.hidden_at is not None:
        return False
    if t.kind in ("DIRECT", "GROUP"):
        return v.internal and _active_participant(db, t.id, v.id) is not None
    if t.kind == "CASE":
        if v.client:
            return t.case is not None and t.case.client_id == v.client.id
        return v.works_cases
    if t.kind == "QUERY":
        if v.client:
            return t.created_by_account_id == v.id
        if not v.works_cases:
            return False
        if v.is_admin or t.owner_account_id in (None, v.id):
            return True
        return _active_participant(db, t.id, v.id) is not None
    return False


def can_post(db: Session, v: Viewer, t: ChatThread) -> bool:
    if not can_view(db, v, t):
        return False
    return not (t.kind == "QUERY" and t.status == "closed" and not v.internal)


def can_note(v: Viewer, t: ChatThread) -> bool:
    """Internal notes: staff and admins on client conversations."""
    return v.works_cases and t.kind in ("CASE", "QUERY")


def _is_group_manager(db: Session, v: Viewer, t: ChatThread) -> bool:
    if t.kind != "GROUP" or not can_view(db, v, t):
        return False
    p = _active_participant(db, t.id, v.id)
    return v.is_admin or bool(p and p.member_role == "owner")


def get_thread(db: Session, v: Viewer, thread_id: str, *, audit: bool = False) -> ChatThread:
    """A thread the viewer may open. ``audit`` lets admins read hidden threads (read only)."""
    t = db.query(ChatThread).filter(ChatThread.id == thread_id).first()
    if not t:
        raise _fail(status.HTTP_404_NOT_FOUND, "Conversation not found.")
    if audit and v.is_admin and t.hidden_at is not None:
        return t
    if not can_view(db, v, t):
        raise _fail(status.HTTP_404_NOT_FOUND, "Conversation not found.")
    return t


# ── messages ──────────────────────────────────────────────────────────────

def _sender_name(db: Session, v: Viewer) -> str:
    return (account_names(db, {v.id}).get(v.id, {}).get("name") or v.code.title())[:80]


def _validate_files(files: list[UploadFile]) -> list[tuple[UploadFile, bytes, str]]:
    # Browsers send an empty part when no file is chosen; keep only real uploads.
    files = [f for f in files if isinstance(f, StarletteUploadFile) and f.filename]
    if len(files) > MAX_FILES:
        raise _fail(status.HTTP_400_BAD_REQUEST, f"Attach up to {MAX_FILES} files per message.")
    checked = []
    limit = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024
    for f in files:
        ext = os.path.splitext(f.filename)[1].lower()
        if ext not in settings.ALLOWED_EXTENSIONS:
            raise _fail(status.HTTP_400_BAD_REQUEST,
                        f"{f.filename}: this file type isn't allowed. Use {', '.join(settings.ALLOWED_EXTENSIONS)}.")
        data = f.file.read()
        if len(data) > limit:
            raise _fail(status.HTTP_400_BAD_REQUEST, f"{f.filename} is larger than {settings.MAX_UPLOAD_SIZE_MB} MB.")
        checked.append((f, data, ext))
    return checked


def _store_attachments(db: Session, thread: ChatThread, message: ChatMessage, files: list[tuple[UploadFile, bytes, str]]) -> None:
    for f, data, ext in files:
        stored = f"{uuid.uuid4().hex}{ext}"
        key = str(Path(settings.CHAT_ATTACHMENTS_DIR) / thread.id / stored)
        path = storage_provider.save(io.BytesIO(data), key, content_type=f.content_type)
        db.add(ChatAttachment(
            message_id=message.id,
            original_filename=os.path.basename(f.filename)[:255],
            stored_filename=stored,
            file_path=path,
            mime_type=f.content_type or "application/octet-stream",
            file_size=len(data),
        ))


def _write(db: Session, thread: ChatThread, *, sender: Viewer | None, body: str, visibility: str = "all",
           kind: str = "text", files: list[tuple[UploadFile, bytes, str]] | None = None) -> ChatMessage:
    """Store one message and update the thread's inbox fields. No permission checks here."""
    msg = ChatMessage(
        thread_id=thread.id,
        sender_account_id=sender.id if sender else None,
        sender_type=sender.code if sender else None,
        sender_name=_sender_name(db, sender) if sender else None,
        kind=kind,
        visibility=visibility,
        body=body,
    )
    db.add(msg)
    db.flush()
    if files:
        _store_attachments(db, thread, msg, files)
    thread.last_message_at = msg.created_at
    if visibility == "all":
        text = body or (f"Attachment: {files[0][0].filename}" if files else "")
        thread.last_message_preview = text[:PREVIEW_LEN]
    if thread.kind in ("DIRECT", "GROUP") and thread.case_id is None:
        # General chats hide after a quiet period; every message pushes it forward.
        thread.hide_after = msg.created_at + _hide_delay()
    return msg


def _system(db: Session, thread: ChatThread, text: str) -> ChatMessage:
    return _write(db, thread, sender=None, body=text, kind="system")


def post_message(db: Session, v: Viewer, t: ChatThread, body: str, visibility: str = "all",
                 files: list[UploadFile] | None = None) -> ChatMessage:
    if not can_post(db, v, t):
        raise _fail(status.HTTP_403_FORBIDDEN, "You can't post in this conversation.")
    body = (body or "").strip()
    if len(body) > BODY_MAX:
        raise _fail(status.HTTP_400_BAD_REQUEST, f"Keep messages under {BODY_MAX} characters.")
    if visibility not in ("all", "internal"):
        raise _fail(status.HTTP_400_BAD_REQUEST, "Unknown message visibility.")
    if visibility == "internal" and not can_note(v, t):
        raise _fail(status.HTTP_403_FORBIDDEN, "Internal notes are for staff on client conversations.")
    checked = _validate_files(files or [])
    if not body and not checked:
        raise _fail(status.HTTP_400_BAD_REQUEST, "Write a message or attach a file.")

    if t.kind in ("CASE", "QUERY") and v.internal and not _active_participant(db, t.id, v.id):
        _add_participant(db, t, v.id, "admin_joined" if v.is_admin else "member")
    if t.kind == "CASE" and v.client:
        ensure_case_team(db, t)
    if t.kind == "QUERY" and v.client and t.status == "resolved":
        _reopen(t)

    msg = _write(db, t, sender=v, body=body, visibility=visibility, files=checked)
    me = _active_participant(db, t.id, v.id)
    if me:
        me.last_read_at = msg.created_at
    db.commit()
    return msg


def list_messages(db: Session, v: Viewer, t: ChatThread, after: datetime | None = None, limit: int = 200) -> list[ChatMessage]:
    q = db.query(ChatMessage).filter(ChatMessage.thread_id == t.id)
    if not v.internal:
        q = q.filter(ChatMessage.visibility == "all")
    if after:
        # ">=": MySQL keeps whole seconds; the page drops messages it already shows.
        return q.filter(ChatMessage.created_at >= after).order_by(ChatMessage.created_at).limit(limit).all()
    return list(reversed(q.order_by(ChatMessage.created_at.desc()).limit(limit).all()))


def mark_read(db: Session, v: Viewer, t: ChatThread) -> None:
    p = _active_participant(db, t.id, v.id)
    if p:
        p.last_read_at = now()
        db.commit()


def _own_message(db: Session, v: Viewer, message_id: str) -> ChatMessage:
    m = db.query(ChatMessage).filter(ChatMessage.id == message_id).first()
    if not m or m.sender_account_id != v.id or m.deleted_at is not None:
        raise _fail(status.HTTP_404_NOT_FOUND, "Message not found.")
    get_thread(db, v, m.thread_id)
    return m


def edit_message(db: Session, v: Viewer, message_id: str, body: str) -> ChatMessage:
    m = _own_message(db, v, message_id)
    if now() - _utc(m.created_at) > EDIT_WINDOW:
        raise _fail(status.HTTP_400_BAD_REQUEST, "Messages can only be edited for 15 minutes.")
    body = (body or "").strip()
    if not body or len(body) > BODY_MAX:
        raise _fail(status.HTTP_400_BAD_REQUEST, "Write a message.")
    m.body = body
    m.edited_at = now()
    t = db.query(ChatThread).filter(ChatThread.id == m.thread_id).first()
    if m.visibility == "all" and _utc(t.last_message_at) == _utc(m.created_at):
        t.last_message_preview = body[:PREVIEW_LEN]
    db.commit()
    return m


def delete_message(db: Session, v: Viewer, message_id: str) -> ChatMessage:
    """Soft delete: hidden in the UI, text kept for audit."""
    m = _own_message(db, v, message_id)
    m.deleted_at = now()
    m.deleted_by_account_id = v.id
    db.add(AuditLog(actor_account_id=v.id, action="chat_message_deleted", entity_type="chat_message", entity_id=m.id))
    db.commit()
    return m


def attachment_for(db: Session, v: Viewer, attachment_id: str) -> ChatAttachment:
    a = db.query(ChatAttachment).filter(ChatAttachment.id == attachment_id).first()
    if not a:
        raise _fail(status.HTTP_404_NOT_FOUND, "File not found.")
    m = db.query(ChatMessage).filter(ChatMessage.id == a.message_id).first()
    t = get_thread(db, v, m.thread_id, audit=True)
    if m.deleted_at is not None or (m.visibility == "internal" and not v.internal) or (t.hidden_at and not v.is_admin):
        raise _fail(status.HTTP_404_NOT_FOUND, "File not found.")
    return a


# ── unread counts ─────────────────────────────────────────────────────────

def unread_by_thread(db: Session, v: Viewer, thread_ids: list[str] | None = None) -> dict[str, int]:
    """Messages from other people since the viewer last read each thread they belong to."""
    q = (
        db.query(ChatMessage.thread_id, func.count(ChatMessage.id))
        .join(ChatParticipant, and_(ChatParticipant.thread_id == ChatMessage.thread_id, ChatParticipant.account_id == v.id))
        .join(ChatThread, ChatThread.id == ChatMessage.thread_id)
        .filter(
            ChatParticipant.left_at.is_(None),
            ChatThread.hidden_at.is_(None),
            ChatMessage.deleted_at.is_(None),
            ChatMessage.sender_account_id.isnot(None),
            ChatMessage.sender_account_id != v.id,
            or_(ChatParticipant.last_read_at.is_(None), ChatMessage.created_at > ChatParticipant.last_read_at),
        )
    )
    if not v.internal:
        q = q.filter(ChatMessage.visibility == "all")
    if thread_ids is not None:
        if not thread_ids:
            return {}
        q = q.filter(ChatMessage.thread_id.in_(thread_ids))
    return dict(q.group_by(ChatMessage.thread_id).all())


def total_unread(db: Session, v: Viewer) -> int:
    return sum(unread_by_thread(db, v).values())


# ── direct and group chats ────────────────────────────────────────────────

def _linked_filing(db: Session, v: Viewer, case_id: str | None) -> TaxFiling | None:
    if not case_id:
        return None
    if not v.works_cases:
        raise _fail(status.HTTP_403_FORBIDDEN, "Only staff and admins can link chats to a filing.")
    filing = db.query(TaxFiling).filter(TaxFiling.id == case_id).first()
    if not filing:
        raise _fail(status.HTTP_404_NOT_FOUND, "Filing not found.")
    return filing


def open_direct(db: Session, v: Viewer, other_account_id: str, case_id: str | None = None) -> ChatThread:
    """Open the one-to-one thread with someone, creating it the first time."""
    if not v.internal:
        raise _fail(status.HTTP_403_FORBIDDEN, "Direct messages are for staff and admins.")
    if other_account_id == v.id:
        raise _fail(status.HTTP_400_BAD_REQUEST, "Pick someone other than yourself.")
    if not _active_internal_account(db, other_account_id):
        raise _fail(status.HTTP_400_BAD_REQUEST, "You can only message active staff and admins.")
    filing = _linked_filing(db, v, case_id)
    key = direct_key_for(v.id, other_account_id, filing.id if filing else None)

    t = db.query(ChatThread).filter(ChatThread.direct_key == key).first()
    if not t:
        t = ChatThread(kind="DIRECT", direct_key=key, case_id=filing.id if filing else None, created_by_account_id=v.id)
        db.add(t)
        try:
            db.flush()
        except IntegrityError:  # the other person opened it at the same moment
            db.rollback()
            t = db.query(ChatThread).filter(ChatThread.direct_key == key).one()
    if t.hidden_at is not None:  # starting the chat again brings it back with its history
        t.hidden_at = None
        t.hide_after = now() + _hide_delay() if t.case_id is None else None
    for account_id in (v.id, other_account_id):
        _add_participant(db, t, account_id, "member")
    db.commit()
    return t


def create_group(db: Session, v: Viewer, name: str, member_ids: list[str], case_id: str | None = None) -> ChatThread:
    if not v.internal:
        raise _fail(status.HTTP_403_FORBIDDEN, "Groups are for staff and admins.")
    name = (name or "").strip()
    if not name or len(name) > 100:
        raise _fail(status.HTTP_400_BAD_REQUEST, "Give the group a name (up to 100 characters).")
    others = {m for m in member_ids if m and m != v.id}
    if len(others) < GROUP_MIN_OTHERS:
        raise _fail(status.HTTP_400_BAD_REQUEST, f"Pick at least {GROUP_MIN_OTHERS} people for a group.")
    for m in others:
        if not _active_internal_account(db, m):
            raise _fail(status.HTTP_400_BAD_REQUEST, "Groups can only include active staff and admins.")
    filing = _linked_filing(db, v, case_id)

    t = ChatThread(kind="GROUP", name=name, case_id=filing.id if filing else None, created_by_account_id=v.id)
    db.add(t)
    db.flush()
    _add_participant(db, t, v.id, "member", role="owner")
    for m in others:
        _add_participant(db, t, m, "member")
    _system(db, t, f"{_sender_name(db, v)} created the group")
    db.commit()
    return t


def _require_manager(db: Session, v: Viewer, t: ChatThread) -> None:
    if not _is_group_manager(db, v, t):
        raise _fail(status.HTTP_403_FORBIDDEN, "Only group owners and admins can change the group.")


def rename_group(db: Session, v: Viewer, t: ChatThread, name: str) -> None:
    _require_manager(db, v, t)
    name = (name or "").strip()
    if not name or len(name) > 100:
        raise _fail(status.HTTP_400_BAD_REQUEST, "Give the group a name (up to 100 characters).")
    t.name = name
    _system(db, t, f"{_sender_name(db, v)} renamed the group to {name}")
    db.commit()


def add_members(db: Session, v: Viewer, t: ChatThread, account_ids: list[str]) -> None:
    _require_manager(db, v, t)
    names = []
    for account_id in {a for a in account_ids if a}:
        if not _active_internal_account(db, account_id):
            raise _fail(status.HTTP_400_BAD_REQUEST, "Groups can only include active staff and admins.")
        if _active_participant(db, t.id, account_id):
            continue
        _add_participant(db, t, account_id, "member")
        names.append(account_names(db, {account_id}).get(account_id, {}).get("name", "someone"))
    if names:
        _system(db, t, f"{_sender_name(db, v)} added {', '.join(names)}")
    db.commit()


def remove_member(db: Session, v: Viewer, t: ChatThread, account_id: str) -> None:
    if account_id == v.id:
        return leave_group(db, v, t)
    _require_manager(db, v, t)
    p = _active_participant(db, t.id, account_id)
    if not p:
        raise _fail(status.HTTP_404_NOT_FOUND, "That person isn't in the group.")
    p.left_at = now()
    name = account_names(db, {account_id}).get(account_id, {}).get("name", "someone")
    _system(db, t, f"{_sender_name(db, v)} removed {name}")
    db.commit()


def leave_group(db: Session, v: Viewer, t: ChatThread) -> None:
    if t.kind != "GROUP":
        raise _fail(status.HTTP_400_BAD_REQUEST, "Only groups can be left.")
    p = _active_participant(db, t.id, v.id)
    if not p:
        raise _fail(status.HTTP_404_NOT_FOUND, "You aren't in this group.")
    p.left_at = now()
    remaining = (
        db.query(ChatParticipant)
        .filter(ChatParticipant.thread_id == t.id, ChatParticipant.left_at.is_(None), ChatParticipant.id != p.id)
        .order_by(ChatParticipant.joined_at)
        .all()
    )
    if p.member_role == "owner" and remaining and not any(r.member_role == "owner" for r in remaining):
        remaining[0].member_role = "owner"  # a group always keeps an owner
    _system(db, t, f"{_sender_name(db, v)} left the group")
    db.commit()


# ── case conversations ────────────────────────────────────────────────────

def _case_staff_accounts(db: Session, case_id: str) -> list[str]:
    """Assigned staff, or every active staff member while cases aren't assigned yet."""
    assigned = [a for (a,) in (
        db.query(Staff.account_id)
        .join(CaseAssignment, CaseAssignment.staff_id == Staff.id)
        .join(AuthAccount, AuthAccount.id == Staff.account_id)
        .filter(CaseAssignment.case_id == case_id, CaseAssignment.status == "active", AuthAccount.is_active.is_(True))
    )]
    if assigned:
        return assigned
    return [a for (a,) in db.query(Staff.account_id).join(AuthAccount, AuthAccount.id == Staff.account_id)
            .filter(AuthAccount.is_active.is_(True))]


def ensure_case_team(db: Session, t: ChatThread) -> None:
    """Make sure the client and the case team are members of a case thread."""
    filing = t.case
    present = set(_active_member_ids(db, t.id))
    client_account = _filing_owner_account(db, filing)
    if client_account and client_account not in present:
        _add_participant(db, t, client_account, "client")
    for account_id in _case_staff_accounts(db, filing.id):
        if account_id not in present:
            _add_participant(db, t, account_id, "case_assignment")


def sync_case_team(db: Session, case_id: str) -> None:
    """Call after changing case_assignments: staff added for the case leave if no longer on it."""
    t = db.query(ChatThread).filter(ChatThread.kind == "CASE", ChatThread.case_id == case_id).first()
    if not t:
        return
    team = set(_case_staff_accounts(db, case_id))
    for p in db.query(ChatParticipant).filter(ChatParticipant.thread_id == t.id, ChatParticipant.left_at.is_(None),
                                              ChatParticipant.added_reason == "case_assignment"):
        if p.account_id not in team:
            p.left_at = now()
    ensure_case_team(db, t)


def _is_terminal(db: Session, stage_code: str) -> bool:
    return bool(db.query(CaseStage.is_terminal).filter(CaseStage.code == stage_code).scalar())


def case_thread(db: Session, v: Viewer, filing_id: str) -> ChatThread:
    """The filing's client conversation, created the first time someone opens it."""
    filing = db.query(TaxFiling).filter(TaxFiling.id == filing_id).with_for_update().first()
    if not filing or (v.client and filing.client_id != v.client.id) or (not v.client and not v.works_cases):
        raise _fail(status.HTTP_404_NOT_FOUND, "Filing not found.")
    t = db.query(ChatThread).filter(ChatThread.kind == "CASE", ChatThread.case_id == filing.id).first()
    if not t:
        t = ChatThread(kind="CASE", case_id=filing.id, created_by_account_id=v.id)
        if _is_terminal(db, filing.status):
            t.hide_after = now() + _hide_delay()
        db.add(t)
        db.flush()
        ensure_case_team(db, t)
        db.commit()
    elif t.hidden_at is not None:
        raise _fail(status.HTTP_404_NOT_FOUND, "This conversation has been archived.")
    return t


def on_case_stage_changed(db: Session, filing: TaxFiling) -> None:
    """Start or cancel the hiding timer of chats linked to a filing. The caller commits."""
    terminal = _is_terminal(db, filing.status)
    for t in db.query(ChatThread).filter(ChatThread.case_id == filing.id, ChatThread.hidden_at.is_(None),
                                         ChatThread.kind != "QUERY"):
        if terminal:
            t.hide_after = t.hide_after or now() + _hide_delay()
        else:
            t.hide_after = None


# ── client queries ────────────────────────────────────────────────────────

def topics(db: Session) -> list[ChatQueryTopic]:
    return db.query(ChatQueryTopic).filter(ChatQueryTopic.is_active.is_(True)).order_by(ChatQueryTopic.sort_order).all()


def create_query(db: Session, v: Viewer, topic_id: int, subject: str, body: str, case_id: str | None = None,
                 files: list[UploadFile] | None = None) -> ChatThread:
    if not v.client:
        raise _fail(status.HTTP_403_FORBIDDEN, "Only clients can ask questions here.")
    topic = db.query(ChatQueryTopic).filter(ChatQueryTopic.id == topic_id, ChatQueryTopic.is_active.is_(True)).first()
    if not topic:
        raise _fail(status.HTTP_400_BAD_REQUEST, "Pick a topic.")
    subject = (subject or "").strip()
    body = (body or "").strip()
    if not subject or len(subject) > 150:
        raise _fail(status.HTTP_400_BAD_REQUEST, "Write a short subject (up to 150 characters).")
    if not body or len(body) > BODY_MAX:
        raise _fail(status.HTTP_400_BAD_REQUEST, "Write your question.")
    filing = None
    if case_id:
        filing = db.query(TaxFiling).filter(TaxFiling.id == case_id, TaxFiling.client_id == v.client.id).first()
        if not filing:
            raise _fail(status.HTTP_404_NOT_FOUND, "Filing not found.")
    checked = _validate_files(files or [])

    t = ChatThread(kind="QUERY", topic_id=topic.id, subject=subject, case_id=filing.id if filing else None,
                   created_by_account_id=v.id, status="open")
    db.add(t)
    db.flush()
    _add_participant(db, t, v.id, "client")
    if filing:
        # Routed to the case team; the earliest-assigned staff member owns it.
        assigned = (
            db.query(Staff.account_id)
            .join(CaseAssignment, CaseAssignment.staff_id == Staff.id)
            .filter(CaseAssignment.case_id == filing.id, CaseAssignment.status == "active")
            .order_by(CaseAssignment.assigned_at)
            .all()
        )
        for i, (account_id,) in enumerate(assigned):
            if i == 0:
                t.owner_account_id = account_id
            _add_participant(db, t, account_id, "query_owner" if i == 0 else "case_assignment")
    _write(db, t, sender=v, body=body, files=checked)
    db.commit()
    return t


def claim_query(db: Session, v: Viewer, t: ChatThread, assignee_id: str | None = None) -> None:
    """Take an unclaimed query, or (admins) hand it to someone."""
    if t.kind != "QUERY" or not v.works_cases or not can_view(db, v, t):
        raise _fail(status.HTTP_403_FORBIDDEN, "You can't take this question.")
    target = assignee_id or v.id
    if target != v.id and not v.is_admin:
        raise _fail(status.HTTP_403_FORBIDDEN, "Only admins can assign questions to someone else.")
    if t.owner_account_id and t.owner_account_id != v.id and not v.is_admin:
        raise _fail(status.HTTP_409_CONFLICT, "Someone else is already handling this question.")
    acc = _active_internal_account(db, target)
    if not acc or not viewer_for(db, acc).works_cases:
        raise _fail(status.HTTP_400_BAD_REQUEST, "Pick an active staff member or admin.")
    t.owner_account_id = target
    _add_participant(db, t, target, "query_owner")
    name = account_names(db, {target}).get(target, {}).get("name", "A team member")
    _system(db, t, f"{name} is handling this question")
    db.commit()


def resolve_query(db: Session, v: Viewer, t: ChatThread) -> None:
    if t.kind != "QUERY" or not v.works_cases or not can_view(db, v, t):
        raise _fail(status.HTTP_403_FORBIDDEN, "You can't resolve this question.")
    t.status = "resolved"
    t.resolved_at = now()
    t.resolved_by_account_id = v.id
    t.hide_after = t.resolved_at + _hide_delay()
    _system(db, t, f"{_sender_name(db, v)} marked this question resolved")
    db.commit()


def _reopen(t: ChatThread) -> None:
    t.status = "open"
    t.resolved_at = None
    t.resolved_by_account_id = None
    t.hide_after = None


def reopen_query(db: Session, v: Viewer, t: ChatThread) -> None:
    if t.kind != "QUERY" or not can_view(db, v, t) or t.status == "open":
        raise _fail(status.HTTP_400_BAD_REQUEST, "This question is already open.")
    _reopen(t)
    _system(db, t, f"{_sender_name(db, v)} reopened this question")
    db.commit()


# ── hiding (retention) ────────────────────────────────────────────────────

def hide_due_threads(db: Session, at: datetime | None = None) -> int:
    """Hide every thread whose hide_after has passed. Run daily (app/tasks/chat_hide.py)."""
    at = at or now()
    count = (
        db.query(ChatThread)
        .filter(ChatThread.hidden_at.is_(None), ChatThread.hide_after.isnot(None), ChatThread.hide_after <= at)
        .update({ChatThread.hidden_at: at}, synchronize_session=False)
    )
    db.commit()
    return count


def hidden_threads(db: Session, v: Viewer, limit: int = 100) -> list[ChatThread]:
    if not v.is_admin:
        raise _fail(status.HTTP_403_FORBIDDEN, "Only admins can see hidden conversations.")
    return db.query(ChatThread).filter(ChatThread.hidden_at.isnot(None)).order_by(ChatThread.hidden_at.desc()).limit(limit).all()


def restore_thread(db: Session, v: Viewer, t: ChatThread) -> None:
    if not v.is_admin or t.hidden_at is None:
        raise _fail(status.HTTP_400_BAD_REQUEST, "Only hidden conversations can be restored, by an admin.")
    t.hidden_at = None
    if t.kind in ("DIRECT", "GROUP") and t.case_id is None:
        t.hide_after = now() + _hide_delay()
    elif t.kind == "QUERY" and t.status != "open":
        t.hide_after = now() + _hide_delay()
    else:
        t.hide_after = now() + _hide_delay() if t.case and _is_terminal(db, t.case.status) else None
    _system(db, t, f"{_sender_name(db, v)} restored this conversation")
    db.commit()


# ── pickers ───────────────────────────────────────────────────────────────

def internal_people(db: Session, v: Viewer, q: str | None = None, case_workers_only: bool = False, limit: int = 30) -> list[dict]:
    """Active staff and admins, for the new-chat and group pickers. Clients never appear."""
    if not v.internal:
        raise _fail(status.HTTP_403_FORBIDDEN, "Only staff and admins can browse people.")
    people = []
    term = f"%{q.strip()}%" if q and q.strip() else None
    for model, label, number in ((Staff, "Staff", "staff_number"), (Admin, "Admin", "admin_number")):
        query = (
            db.query(model, AuthAccount.email)
            .join(AuthAccount, AuthAccount.id == model.account_id)
            .filter(AuthAccount.is_active.is_(True), model.account_id != v.id)
        )
        if term:
            query = query.filter(or_(model.first_name.ilike(term), model.last_name.ilike(term), AuthAccount.email.ilike(term)))
        for person, email in query.order_by(model.first_name).limit(limit).all():
            name = " ".join(p for p in (person.first_name, person.last_name) if p)
            people.append({"account_id": person.account_id, "name": name, "initials": _initials(name),
                           "type": label, "number": getattr(person, number), "email": email})
    people.sort(key=lambda p: p["name"].lower())
    return people[:limit]


def find_cases(db: Session, v: Viewer, q: str | None = None, limit: int = 15) -> list[dict]:
    """Filings a chat can be linked to. Clients get their own; staff and admins search all."""
    query = db.query(TaxFiling)
    if v.client:
        query = query.filter(TaxFiling.client_id == v.client.id)
    elif not v.works_cases:
        return []
    elif q and q.strip():
        term = f"%{q.strip()}%"
        query = query.filter(or_(TaxFiling.case_number.ilike(term), TaxFiling.first_name.ilike(term),
                                 TaxFiling.last_name.ilike(term)))
    return [{"id": f.id, "case_number": f.case_number, "tax_year": f.tax_year,
             "client_name": " ".join(p for p in (f.first_name, f.last_name) if p)}
            for f in query.order_by(TaxFiling.created_at.desc()).limit(limit).all()]


# ── shaping data for the page ─────────────────────────────────────────────

def _case_url(v: Viewer, case_id: str) -> str:
    if v.client:
        return "/client/filings"
    return f"/admin/cases/{case_id}" if v.is_admin else f"/staff/cases/{case_id}"


def _title(v: Viewer, t: ChatThread, names: dict[str, dict], members: list[str]) -> tuple[str, str]:
    """(title, subtitle) as this viewer should see the thread."""
    case = t.case
    case_label = case.case_number if case else ""
    if t.kind == "DIRECT":
        other = next((m for m in members if m != v.id), None)
        if other is None:  # the other person left; fall back to everyone who was ever in it
            other = next((p for p in names if p != v.id), None)
        person = names.get(other, {})
        return person.get("name", "Former member"), (person.get("role", "").title() + (f" · {case_label}" if case_label else ""))
    if t.kind == "GROUP":
        return t.name or "Group", f"{len(members)} members" + (f" · {case_label}" if case_label else "")
    if t.kind == "CASE":
        if v.client:
            return f"Tax return {case.tax_year} · {case_label}", "Your tax team"
        client_name = " ".join(p for p in (case.first_name, case.last_name) if p)
        return f"{client_name} · {case_label}", f"Tax year {case.tax_year} · case chat with client"
    topic = t.topic.name if t.topic else "Question"
    return t.subject or "Question", f"{topic}" + (f" · {case_label}" if case_label else "")


def serialize_threads(db: Session, v: Viewer, threads: list[ChatThread], unread: dict[str, int] | None = None) -> list[dict]:
    if not threads:
        return []
    ids = [t.id for t in threads]
    rows = db.query(ChatParticipant.thread_id, ChatParticipant.account_id, ChatParticipant.left_at).filter(
        ChatParticipant.thread_id.in_(ids)).all()
    everyone = {r.account_id for r in rows} | {t.owner_account_id for t in threads if t.owner_account_id}
    names = account_names(db, everyone)
    active: dict[str, list[str]] = {}
    ever: dict[str, set[str]] = {}
    for r in rows:
        ever.setdefault(r.thread_id, set()).add(r.account_id)
        if r.left_at is None:
            active.setdefault(r.thread_id, []).append(r.account_id)
    unread = unread if unread is not None else unread_by_thread(db, v, ids)

    out = []
    for t in threads:
        members = active.get(t.id, [])
        thread_names = {a: names[a] for a in ever.get(t.id, ()) if a in names}
        title, subtitle = _title(v, t, thread_names, members)
        out.append({
            "id": t.id,
            "number": t.thread_number,
            "kind": t.kind,
            "title": title,
            "subtitle": subtitle,
            "initials": _initials(title) if t.kind == "DIRECT" else None,
            "case": {"id": t.case.id, "case_number": t.case.case_number, "tax_year": t.case.tax_year,
                     "url": _case_url(v, t.case.id)} if t.case else None,
            "status": t.status,
            "owner": names.get(t.owner_account_id, {}).get("name") if t.owner_account_id else None,
            "unclaimed": t.kind == "QUERY" and t.owner_account_id is None,
            "preview": t.last_message_preview or "",
            "last_message_at": iso(t.last_message_at or t.created_at),
            "unread": unread.get(t.id, 0),
            "hidden": t.hidden_at is not None,
            "member_initials": [_initials(names.get(m, {}).get("name", "?")) for m in members[:4]],
            "member_count": len(members),
        })
    return out


def inbox(db: Session, v: Viewer) -> dict:
    """The left-hand list, split into the sections this account type sees."""
    mine = (
        db.query(ChatThread)
        .join(ChatParticipant, and_(ChatParticipant.thread_id == ChatThread.id, ChatParticipant.account_id == v.id))
        .filter(ChatParticipant.left_at.is_(None), ChatThread.hidden_at.is_(None))
        .all()
    )
    threads = {t.id: t for t in mine}
    if v.client:
        # Every query the client raised, even before anyone answered
        for t in db.query(ChatThread).filter(ChatThread.kind == "QUERY", ChatThread.created_by_account_id == v.id,
                                             ChatThread.hidden_at.is_(None)):
            threads.setdefault(t.id, t)
    if v.works_cases:
        for t in db.query(ChatThread).filter(ChatThread.kind == "QUERY", ChatThread.owner_account_id.is_(None),
                                             ChatThread.status == "open", ChatThread.hidden_at.is_(None)):
            threads.setdefault(t.id, t)

    items = serialize_threads(db, v, list(threads.values()))
    items.sort(key=lambda i: i["last_message_at"] or "", reverse=True)
    by_kind: dict[str, list[dict]] = {}
    for i in items:
        by_kind.setdefault(i["kind"], []).append(i)

    if v.client:
        sections = [
            {"key": "filings", "label": "My filings", "items": by_kind.get("CASE", [])},
            {"key": "questions", "label": "My questions", "items": by_kind.get("QUERY", [])},
        ]
        with_chat = {i["case"]["id"] for i in by_kind.get("CASE", []) if i["case"]}
        start = [f for f in find_cases(db, v, limit=50) if f["id"] not in with_chat]
        return {"sections": sections, "filings_without_chat": start}

    sections = [
        {"key": "direct", "label": "Direct messages", "items": by_kind.get("DIRECT", []), "can_add": True},
        {"key": "groups", "label": "Groups", "items": by_kind.get("GROUP", []), "can_add": True},
    ]
    if v.works_cases:
        sections += [
            {"key": "cases", "label": "Case chats with clients", "items": by_kind.get("CASE", [])},
            {"key": "queries", "label": "Client queries", "items": by_kind.get("QUERY", [])},
        ]
    return {"sections": sections, "filings_without_chat": []}


def thread_detail(db: Session, v: Viewer, t: ChatThread) -> dict:
    item = serialize_threads(db, v, [t])[0]
    rows = db.query(ChatParticipant).filter(ChatParticipant.thread_id == t.id, ChatParticipant.left_at.is_(None)).all()
    names = account_names(db, {r.account_id for r in rows})
    item["members"] = [
        {"account_id": r.account_id, "name": names.get(r.account_id, {}).get("name", "Unknown"),
         "role": names.get(r.account_id, {}).get("role", ""), "owner": r.member_role == "owner",
         "initials": _initials(names.get(r.account_id, {}).get("name", "?"))}
        for r in rows
    ] if v.internal else []  # clients see "your tax team", not staff names
    item["topic"] = t.topic.name if t.topic else None
    read_only = t.hidden_at is not None
    item["perms"] = {
        "post": not read_only and can_post(db, v, t),
        "note": not read_only and can_note(v, t),
        "manage": not read_only and _is_group_manager(db, v, t),
        "leave": not read_only and t.kind == "GROUP" and _active_participant(db, t.id, v.id) is not None,
        "claim": not read_only and t.kind == "QUERY" and v.works_cases and t.owner_account_id != v.id
                 and (t.owner_account_id is None or v.is_admin),
        "assign": not read_only and t.kind == "QUERY" and v.is_admin,
        "resolve": not read_only and t.kind == "QUERY" and v.works_cases and t.status == "open",
        "reopen": not read_only and t.kind == "QUERY" and t.status != "open",
        "restore": read_only and v.is_admin,
    }
    item["read_only"] = read_only
    return item


def serialize_messages(db: Session, v: Viewer, messages: list[ChatMessage]) -> list[dict]:
    ids = [m.id for m in messages]
    files: dict[str, list[dict]] = {}
    if ids:
        for a in db.query(ChatAttachment).filter(ChatAttachment.message_id.in_(ids)):
            files.setdefault(a.message_id, []).append({
                "id": a.id, "name": a.original_filename, "size": a.file_size,
                "url": f"/chat/attachments/{a.id}", "saved": bool(a.filing_document_id),
            })
    out = []
    for m in messages:
        deleted = m.deleted_at is not None
        mine = m.sender_account_id == v.id
        out.append({
            "id": m.id,
            "kind": m.kind,
            "mine": mine,
            "sender": m.sender_name if (v.internal or m.sender_type == "CLIENT") else "Your tax team",
            "sender_type": m.sender_type,
            "visibility": m.visibility,
            "body": "" if deleted else m.body,
            "deleted": deleted,
            "edited": m.edited_at is not None,
            "created_at": iso(m.created_at),
            "can_edit": mine and not deleted and m.kind == "text" and now() - _utc(m.created_at) <= EDIT_WINDOW,
            "attachments": [] if deleted else files.get(m.id, []),
        })
    return out
