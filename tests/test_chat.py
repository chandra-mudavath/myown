"""Chat: access rules and main flows for client, staff and admin, against an in-memory SQLite database."""
from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.db_models  # noqa: F401
from app.core.config import settings
from app.core.database import Base, get_db
from app.core.security import create_access_token
from app.main import app
from app.modules.admin.models.admin import Admin
from app.platform.models.auth import AccountType, AuthAccount
from app.platform.models.chat import ChatThread
from app.modules.client.models.client import Client
from app.platform.models.lookups import AccountTypeLookup, CaseStage, ChatQueryTopic
from app.modules.staff.models.staff import Staff
from app.platform.models.tax_filing import TaxFiling
from app.platform.services import chat_service


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "CHAT_ATTACHMENTS_DIR", str(tmp_path / "chat"))
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    event.listen(engine, "connect", lambda c, _: c.execute("PRAGMA foreign_keys=ON"))
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    def _db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _db
    db = Session()
    for i, (code, internal, cases) in enumerate([("CLIENT", False, True), ("STAFF", True, True), ("ADMIN", True, True), ("HR", True, False)], 1):
        db.add(AccountTypeLookup(id=i, code=code, name=code.title(), number_prefix=code[:3], portal_path="/" + code.lower(),
                                 has_case_access=cases, is_internal=internal, sort_order=i))
    db.add_all([
        CaseStage(id=1, code="pending_review", name="Registered", client_status="Submitted", sort_order=1),
        CaseStage(id=2, code="complete", name="Completed", client_status="Completed", is_terminal=True, sort_order=2),
        ChatQueryTopic(id=1, code="REFUND", name="Refund", sort_order=1),
    ])

    people = {}

    def account(key, kind, model, first):
        acc = AuthAccount(email=f"{key}@x.test", password_hash="x", account_type=kind, is_active=True, is_verified=True)
        db.add(acc)
        db.flush()
        extra = {"phone": "1"} if model is Client else {}
        person = model(account_id=acc.id, first_name=first, last_name="T", **extra)
        db.add(person)
        db.flush()
        people[key] = {"account": acc, "person": person,
                       "cookie": {"access_token": create_access_token(acc.email, kind.value)}}

    account("client", AccountType.CLIENT, Client, "Mike")
    account("other_client", AccountType.CLIENT, Client, "Olga")
    account("staff", AccountType.STAFF, Staff, "Rahul")
    account("staff2", AccountType.STAFF, Staff, "Sana")
    account("admin", AccountType.ADMIN, Admin, "Priya")
    filing = TaxFiling(client_id=people["client"]["person"].id, tax_year=2025, filing_type="individual",
                       first_name="Mike", last_name="T", email="client@x.test", status="pending_review")
    db.add(filing)
    db.commit()
    people["filing_id"] = filing.id
    people["Session"] = Session
    yield people
    app.dependency_overrides.clear()
    db.close()


def as_(env, key) -> TestClient:
    c = TestClient(app)
    c.cookies.update(env[key]["cookie"])
    return c


def acc_id(env, key) -> str:
    return env[key]["account"].id


def test_direct_chat_internal_only(env):
    staff, admin, client = as_(env, "staff"), as_(env, "admin"), as_(env, "client")
    t1 = staff.post("/chat/api/direct", json={"account_id": acc_id(env, "admin")}).json()["thread_id"]
    t2 = admin.post("/chat/api/direct", json={"account_id": acc_id(env, "staff")}).json()["thread_id"]
    assert t1 == t2  # one general thread per pair
    # a separate thread for a filing they discuss
    t3 = staff.post("/chat/api/direct", json={"account_id": acc_id(env, "admin"), "case_id": env["filing_id"]}).json()["thread_id"]
    assert t3 != t1
    assert staff.post("/chat/api/direct", json={"account_id": acc_id(env, "client")}).status_code == 400
    assert client.post("/chat/api/direct", json={"account_id": acc_id(env, "staff")}).status_code == 403
    assert client.get(f"/chat/api/threads/{t1}").status_code == 404
    assert as_(env, "staff2").get(f"/chat/api/threads/{t1}").status_code == 404
    assert client.get("/chat/api/people").status_code == 403


def test_group_rules(env):
    staff = as_(env, "staff")
    assert staff.post("/chat/api/groups", json={"name": "Team", "member_ids": [acc_id(env, "admin")]}).status_code == 400
    bad = staff.post("/chat/api/groups", json={"name": "Team", "member_ids": [acc_id(env, "admin"), acc_id(env, "client")]})
    assert bad.status_code == 400
    t = staff.post("/chat/api/groups", json={"name": "Team", "member_ids": [acc_id(env, "admin"), acc_id(env, "staff2")]}).json()["thread_id"]
    detail = staff.get(f"/chat/api/threads/{t}").json()
    assert detail["kind"] == "GROUP" and len(detail["members"]) == 3 and detail["perms"]["manage"]
    s2 = as_(env, "staff2")
    assert not s2.get(f"/chat/api/threads/{t}").json()["perms"]["manage"]
    assert s2.post(f"/chat/api/groups/{t}/rename", json={"name": "X"}).status_code == 403
    assert staff.post(f"/chat/api/groups/{t}/members/{acc_id(env, 'staff2')}/remove").status_code == 200
    assert s2.get(f"/chat/api/threads/{t}").status_code == 404


def test_case_chat_notes_and_unread(env):
    client, staff, staff2 = as_(env, "client"), as_(env, "staff"), as_(env, "staff2")
    fid = env["filing_id"]
    assert as_(env, "other_client").post(f"/chat/api/cases/{fid}/open").status_code == 404
    t = client.post(f"/chat/api/cases/{fid}/open").json()["thread_id"]
    assert staff.post(f"/chat/api/cases/{fid}/open").json()["thread_id"] == t  # one per case

    assert client.post(f"/chat/api/threads/{t}/messages", data={"body": "Do you need my 1099?"}).status_code == 200
    assert staff.get("/chat/api/unread").json()["total"] == 1  # staff are on the case team
    assert staff2.get("/chat/api/unread").json()["total"] == 1
    staff.get(f"/chat/api/threads/{t}/messages")  # reading marks it read
    assert staff.get("/chat/api/unread").json()["total"] == 0

    assert staff.post(f"/chat/api/threads/{t}/messages", data={"body": "No Schedule B needed", "visibility": "internal"}).status_code == 200
    assert client.post(f"/chat/api/threads/{t}/messages", data={"body": "x", "visibility": "internal"}).status_code == 403
    assert staff.post(f"/chat/api/threads/{t}/messages", data={"body": "Yes please upload it"}).status_code == 200

    bodies = [m["body"] for m in client.get(f"/chat/api/threads/{t}/messages").json()["messages"]]
    assert "No Schedule B needed" not in bodies and "Yes please upload it" in bodies
    senders = {m["sender"] for m in client.get(f"/chat/api/threads/{t}/messages").json()["messages"] if not m["mine"]}
    assert senders == {"Your tax team"}  # clients don't see staff names
    assert client.get("/chat/api/unread").json()["total"] == 0  # already read when listed

    internal = [m for m in staff2.get(f"/chat/api/threads/{t}/messages").json()["messages"] if m["visibility"] == "internal"]
    assert len(internal) == 1
    inbox = client.get("/chat/api/inbox").json()
    assert [s["key"] for s in inbox["sections"]] == ["filings", "questions"]
    assert inbox["sections"][0]["items"][0]["preview"] == "Yes please upload it"  # notes never in client preview


def test_query_claim_resolve_reopen(env):
    client, staff, staff2, admin = as_(env, "client"), as_(env, "staff"), as_(env, "staff2"), as_(env, "admin")
    r = client.post("/chat/api/queries", data={"topic_id": 1, "subject": "Refund date?", "body": "When?", "case_id": env["filing_id"]})
    t = r.json()["thread_id"]
    queries = next(s for s in staff.get("/chat/api/inbox").json()["sections"] if s["key"] == "queries")
    assert queries["items"][0]["unclaimed"]
    assert staff.post(f"/chat/api/threads/{t}/claim", json={}).status_code == 200
    assert staff2.post(f"/chat/api/threads/{t}/claim", json={}).status_code == 404  # claimed: no longer visible to them
    assert staff.post(f"/chat/api/threads/{t}/claim", json={"account_id": acc_id(env, "staff2")}).status_code == 403
    assert admin.post(f"/chat/api/threads/{t}/claim", json={"account_id": acc_id(env, "staff2")}).status_code == 200
    assert staff2.post(f"/chat/api/threads/{t}/resolve").status_code == 200
    with env["Session"]() as db:
        assert db.get(ChatThread, t).hide_after is not None
    client.post(f"/chat/api/threads/{t}/messages", data={"body": "Still waiting"})
    assert client.get(f"/chat/api/threads/{t}").json()["status"] == "open"
    with env["Session"]() as db:
        assert db.get(ChatThread, t).hide_after is None


def test_attachments(env):
    client, staff, other = as_(env, "client"), as_(env, "staff"), as_(env, "other_client")
    t = client.post(f"/chat/api/cases/{env['filing_id']}/open").json()["thread_id"]
    bad = client.post(f"/chat/api/threads/{t}/messages", data={"body": ""}, files={"files": ("x.exe", b"MZ", "application/octet-stream")})
    assert bad.status_code == 400
    ok = client.post(f"/chat/api/threads/{t}/messages", data={"body": ""}, files={"files": ("w2.txt", b"wages", "text/plain")})
    att = ok.json()["message"]["attachments"][0]
    assert staff.get(att["url"]).content == b"wages"
    assert other.get(att["url"]).status_code == 404
    note = staff.post(f"/chat/api/threads/{t}/messages", data={"body": "", "visibility": "internal"},
                      files={"files": ("calc.txt", b"internal", "text/plain")}).json()["message"]
    assert client.get(note["attachments"][0]["url"]).status_code == 404


def test_edit_delete_own_only(env):
    client, staff = as_(env, "client"), as_(env, "staff")
    t = client.post(f"/chat/api/cases/{env['filing_id']}/open").json()["thread_id"]
    m = client.post(f"/chat/api/threads/{t}/messages", data={"body": "typo"}).json()["message"]
    assert staff.post(f"/chat/api/messages/{m['id']}/edit", json={"body": "hack"}).status_code == 404
    assert client.post(f"/chat/api/messages/{m['id']}/edit", json={"body": "fixed"}).json()["message"]["edited"]
    gone = client.post(f"/chat/api/messages/{m['id']}/delete").json()["message"]
    assert gone["deleted"] and gone["body"] == ""


def test_stage_change_hides_and_admin_restores(env):
    client, staff, admin = as_(env, "client"), as_(env, "staff"), as_(env, "admin")
    fid = env["filing_id"]
    t = client.post(f"/chat/api/cases/{fid}/open").json()["thread_id"]
    staff.post(f"/staff/cases/{fid}/stage", data={"to": "complete", "next": "/staff/cases"}, follow_redirects=False)
    with env["Session"]() as db:
        assert db.get(ChatThread, t).hide_after is not None
        assert chat_service.hide_due_threads(db, chat_service.now() + timedelta(days=91)) == 1
    assert client.get(f"/chat/api/threads/{t}").status_code == 404
    assert staff.get(f"/chat/api/threads/{t}").status_code == 404
    assert [i["id"] for i in admin.get("/chat/api/hidden").json()["items"]] == [t]
    assert admin.get(f"/chat/api/threads/{t}").json()["read_only"]  # audit view
    assert staff.get("/chat/api/hidden").status_code == 403
    assert admin.post(f"/chat/api/threads/{t}/restore").status_code == 200
    assert client.get(f"/chat/api/threads/{t}").status_code == 200


def test_pages_render(env):
    for key, path in (("staff", "/staff/messages"), ("admin", "/admin/messages"), ("client", "/client/messages")):
        r = as_(env, key).get(path)
        assert r.status_code == 200, (path, r.text[:300])
        assert 'data-chat data-portal="' + path.split("/")[1] + '"' in r.text
    assert as_(env, "client").get("/staff/messages").status_code == 403


def test_ask_form_without_files(env):
    """Browsers send an empty file part when no file is chosen."""
    r = as_(env, "client").post("/chat/api/queries", data={"topic_id": 1, "subject": "Hi", "body": "Question", "case_id": ""},
                                files={"files": ("", b"", "application/octet-stream")})
    assert r.status_code == 200, r.text
