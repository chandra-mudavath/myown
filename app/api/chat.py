"""Chat pages for each portal, and the JSON API the chat page (app/static/js/chat.js) calls.

Every rule lives in app.services.chat_service; these routes only translate HTTP to it.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.dashboard import client_shell_context
from app.api.staff import _nav_context
from app.core.config import settings
from app.core.database import get_db
from app.core.deps import AdminAccount, ClientAccount, StaffAccount, VerifiedAccount
from app.core.templates import templates
from app.models.admin import Admin
from app.models.staff import Staff
from app.services import chat_service as chat

router = APIRouter(tags=["chat"])


# ── pages ─────────────────────────────────────────────────────────────────

def _chat_context(request: Request, thread: str | None, case: str | None) -> dict:
    return {"request": request, "app_name": settings.APP_NAME, "open_thread": thread or "", "open_case": case or ""}


@router.get("/staff/messages", response_class=HTMLResponse, name="staff_messages")
def staff_messages(request: Request, account: StaffAccount, thread: str | None = None, case: str | None = None,
                   db: Session = Depends(get_db)):
    staff = db.query(Staff).filter(Staff.account_id == account.id).first()
    return templates.TemplateResponse("staff/messages.html", {
        **_chat_context(request, thread, case), "account": account, "staff": staff,
        **_nav_context(db, staff), "active_nav": "messages",
    })


@router.get("/admin/messages", response_class=HTMLResponse, name="admin_messages")
def admin_messages(request: Request, account: AdminAccount, thread: str | None = None, case: str | None = None,
                   db: Session = Depends(get_db)):
    admin = db.query(Admin).filter(Admin.account_id == account.id).first()
    return templates.TemplateResponse("admin/messages.html", {
        **_chat_context(request, thread, case), "account": account, "admin": admin, "active_nav": "messages",
    })


@router.get("/client/messages", response_class=HTMLResponse, name="client_messages")
def client_messages(request: Request, account: ClientAccount, thread: str | None = None, case: str | None = None,
                    tax_year: str | None = None, db: Session = Depends(get_db)):
    return templates.TemplateResponse("client/messages.html", {
        **_chat_context(request, thread, case), **client_shell_context(db, account, tax_year), "active_nav": "messages",
    })


# ── JSON API ──────────────────────────────────────────────────────────────

def _viewer(db: Session, account) -> chat.Viewer:
    return chat.viewer_for(db, account)


class DirectIn(BaseModel):
    account_id: str
    case_id: str | None = None


class GroupIn(BaseModel):
    name: str = Field(max_length=100)
    member_ids: list[str]
    case_id: str | None = None


class MembersIn(BaseModel):
    account_ids: list[str]


class NameIn(BaseModel):
    name: str = Field(max_length=100)


class BodyIn(BaseModel):
    body: str


class AssignIn(BaseModel):
    account_id: str | None = None


@router.get("/chat/api/inbox")
def api_inbox(account: VerifiedAccount, db: Session = Depends(get_db)):
    v = _viewer(db, account)
    data = chat.inbox(db, v)
    data["me"] = {"code": v.code, "internal": v.internal, "works_cases": v.works_cases, "admin": v.is_admin}
    return data


@router.get("/chat/api/unread")
def api_unread(account: VerifiedAccount, db: Session = Depends(get_db)):
    return {"total": chat.total_unread(db, _viewer(db, account))}


@router.get("/chat/api/threads/{thread_id}")
def api_thread(thread_id: str, account: VerifiedAccount, db: Session = Depends(get_db)):
    v = _viewer(db, account)
    return chat.thread_detail(db, v, chat.get_thread(db, v, thread_id, audit=True))


@router.get("/chat/api/threads/{thread_id}/messages")
def api_messages(thread_id: str, account: VerifiedAccount, after: str | None = None, db: Session = Depends(get_db)):
    v = _viewer(db, account)
    t = chat.get_thread(db, v, thread_id, audit=True)
    since = None
    if after:
        try:
            since = datetime.fromisoformat(after.replace("Z", "+00:00")).astimezone(timezone.utc)
        except ValueError:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Bad 'after' time.")
    messages = chat.list_messages(db, v, t, after=since)
    if t.hidden_at is None:
        chat.mark_read(db, v, t)
    return {"messages": chat.serialize_messages(db, v, messages)}


@router.post("/chat/api/threads/{thread_id}/messages")
def api_post_message(thread_id: str, account: VerifiedAccount, body: str = Form(""), visibility: str = Form("all"),
                     files: list[UploadFile | str] = File(default=[]), db: Session = Depends(get_db)):
    v = _viewer(db, account)
    t = chat.get_thread(db, v, thread_id)
    msg = chat.post_message(db, v, t, body, visibility, files)
    return {"message": chat.serialize_messages(db, v, [msg])[0]}


@router.post("/chat/api/messages/{message_id}/edit")
def api_edit_message(message_id: str, data: BodyIn, account: VerifiedAccount, db: Session = Depends(get_db)):
    v = _viewer(db, account)
    return {"message": chat.serialize_messages(db, v, [chat.edit_message(db, v, message_id, data.body)])[0]}


@router.post("/chat/api/messages/{message_id}/delete")
def api_delete_message(message_id: str, account: VerifiedAccount, db: Session = Depends(get_db)):
    v = _viewer(db, account)
    return {"message": chat.serialize_messages(db, v, [chat.delete_message(db, v, message_id)])[0]}


@router.post("/chat/api/direct")
def api_open_direct(data: DirectIn, account: VerifiedAccount, db: Session = Depends(get_db)):
    v = _viewer(db, account)
    return {"thread_id": chat.open_direct(db, v, data.account_id, data.case_id).id}


@router.post("/chat/api/groups")
def api_create_group(data: GroupIn, account: VerifiedAccount, db: Session = Depends(get_db)):
    v = _viewer(db, account)
    return {"thread_id": chat.create_group(db, v, data.name, data.member_ids, data.case_id).id}


@router.post("/chat/api/groups/{thread_id}/rename")
def api_rename_group(thread_id: str, data: NameIn, account: VerifiedAccount, db: Session = Depends(get_db)):
    v = _viewer(db, account)
    chat.rename_group(db, v, chat.get_thread(db, v, thread_id), data.name)
    return {"ok": True}


@router.post("/chat/api/groups/{thread_id}/members")
def api_add_members(thread_id: str, data: MembersIn, account: VerifiedAccount, db: Session = Depends(get_db)):
    v = _viewer(db, account)
    chat.add_members(db, v, chat.get_thread(db, v, thread_id), data.account_ids)
    return {"ok": True}


@router.post("/chat/api/groups/{thread_id}/members/{member_id}/remove")
def api_remove_member(thread_id: str, member_id: str, account: VerifiedAccount, db: Session = Depends(get_db)):
    v = _viewer(db, account)
    chat.remove_member(db, v, chat.get_thread(db, v, thread_id), member_id)
    return {"ok": True}


@router.post("/chat/api/groups/{thread_id}/leave")
def api_leave_group(thread_id: str, account: VerifiedAccount, db: Session = Depends(get_db)):
    v = _viewer(db, account)
    chat.leave_group(db, v, chat.get_thread(db, v, thread_id))
    return {"ok": True}


@router.post("/chat/api/cases/{filing_id}/open")
def api_open_case(filing_id: str, account: VerifiedAccount, db: Session = Depends(get_db)):
    v = _viewer(db, account)
    return {"thread_id": chat.case_thread(db, v, filing_id).id}


@router.post("/chat/api/queries")
def api_create_query(account: VerifiedAccount, topic_id: int = Form(...), subject: str = Form(...), body: str = Form(...),
                     case_id: str = Form(""), files: list[UploadFile | str] = File(default=[]), db: Session = Depends(get_db)):
    v = _viewer(db, account)
    return {"thread_id": chat.create_query(db, v, topic_id, subject, body, case_id or None, files).id}


@router.post("/chat/api/threads/{thread_id}/claim")
def api_claim(thread_id: str, data: AssignIn, account: VerifiedAccount, db: Session = Depends(get_db)):
    v = _viewer(db, account)
    chat.claim_query(db, v, chat.get_thread(db, v, thread_id), data.account_id)
    return {"ok": True}


@router.post("/chat/api/threads/{thread_id}/resolve")
def api_resolve(thread_id: str, account: VerifiedAccount, db: Session = Depends(get_db)):
    v = _viewer(db, account)
    chat.resolve_query(db, v, chat.get_thread(db, v, thread_id))
    return {"ok": True}


@router.post("/chat/api/threads/{thread_id}/reopen")
def api_reopen(thread_id: str, account: VerifiedAccount, db: Session = Depends(get_db)):
    v = _viewer(db, account)
    chat.reopen_query(db, v, chat.get_thread(db, v, thread_id))
    return {"ok": True}


@router.get("/chat/api/hidden")
def api_hidden(account: VerifiedAccount, db: Session = Depends(get_db)):
    v = _viewer(db, account)
    return {"items": chat.serialize_threads(db, v, chat.hidden_threads(db, v), unread={})}


@router.post("/chat/api/threads/{thread_id}/restore")
def api_restore(thread_id: str, account: VerifiedAccount, db: Session = Depends(get_db)):
    v = _viewer(db, account)
    chat.restore_thread(db, v, chat.get_thread(db, v, thread_id, audit=True))
    return {"ok": True}


@router.get("/chat/api/people")
def api_people(account: VerifiedAccount, q: str | None = None, db: Session = Depends(get_db)):
    return {"people": chat.internal_people(db, _viewer(db, account), q)}


@router.get("/chat/api/cases")
def api_cases(account: VerifiedAccount, q: str | None = None, db: Session = Depends(get_db)):
    return {"cases": chat.find_cases(db, _viewer(db, account), q)}


@router.get("/chat/api/topics")
def api_topics(account: VerifiedAccount, db: Session = Depends(get_db)):
    return {"topics": [{"id": t.id, "code": t.code, "name": t.name} for t in chat.topics(db)]}


@router.get("/chat/attachments/{attachment_id}")
def chat_attachment(attachment_id: str, account: VerifiedAccount, download: bool = False, db: Session = Depends(get_db)):
    a = chat.attachment_for(db, _viewer(db, account), attachment_id)
    return FileResponse(path=a.file_path, filename=a.original_filename, media_type=a.mime_type,
                        content_disposition_type="attachment" if download else "inline")
