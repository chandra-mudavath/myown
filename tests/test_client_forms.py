"""Client forms that used to be UI only (data-cx-demo): e-file authorization, Filing Status request, FBAR,
Visa & residency and Dependents. Each now saves for the selected tax year and shows on the staff case page."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.db_models  # noqa: F401
from app.core.database import Base, get_db
from app.core.security import create_access_token
from app.main import app
from app.modules.client.models.client import Client
from app.modules.staff.models.staff import Staff, StaffRole
from app.platform.models.auth import AccountType, AuthAccount
from app.platform.models.chat import ChatMessage, ChatThread
from app.platform.models.lookups import AccountTypeLookup, CaseStage, ChatQueryTopic, TaxYear
from app.platform.models.tax_filing import TaxFiling
from app.platform.services import intake


@pytest.fixture()
def env():
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
    for i, (code, internal) in enumerate([("CLIENT", False), ("STAFF", True), ("ADMIN", True)], 1):
        db.add(AccountTypeLookup(id=i, code=code, name=code.title(), number_prefix=code[:3], portal_path="/" + code.lower(),
                                 has_case_access=True, is_internal=internal, sort_order=i))
    db.add_all([
        CaseStage(id=1, code="pending_review", name="Registered", client_status="Submitted", sort_order=1),
        TaxYear(year=2025, name="2025", is_open=True, is_default=True, is_active=True, sort_order=1),
        ChatQueryTopic(id=1, code="FILING_STATUS", name="Filing status", sort_order=1, is_active=True),
    ])
    out = {"Session": Session}
    for key, kind in (("client", AccountType.CLIENT), ("staff", AccountType.STAFF)):
        acc = AuthAccount(email=f"{key}@x.test", password_hash="x", account_type=kind, is_active=True, is_verified=True)
        db.add(acc)
        db.flush()
        if kind == AccountType.CLIENT:
            db.add(Client(account_id=acc.id, first_name="Mike", last_name="T", phone="1"))
        else:
            db.add(Staff(account_id=acc.id, first_name="Sam", last_name="P", role=StaffRole.PREPARER))
        out[key] = {"tax_year": "2025", "access_token": create_access_token(acc.email, kind.value)}
    db.commit()
    db.close()
    yield out
    app.dependency_overrides.clear()


def as_(env, key) -> TestClient:
    c = TestClient(app)
    c.cookies.update(env[key])
    return c


def _section(env, name):
    db = env["Session"]()
    try:
        filing = db.query(TaxFiling).one()
        return intake.section(db, filing, name), filing.id
    finally:
        db.close()


def test_efile_authorization_is_saved_and_shown_to_staff(env):
    client = as_(env, "client")
    r = client.post("/client/efile-authorization", data={"taxpayer_pin": "00000", "consent_efile": "on"})
    assert "5 digits and not all zeros" in r.text  # the error shows on the page after the redirect
    r = client.post("/client/efile-authorization", data={"taxpayer_pin": "12345", "consent_efile": "on"}, follow_redirects=False)
    assert r.status_code == 303 and "saved=" in r.headers["location"]
    row, filing_id = _section(env, intake.EFILE)
    assert row.data["taxpayer_pin"] == "12345" and row.submitted_at
    assert "Signed" in client.get("/client/efile-authorization").text
    assert "12345" in as_(env, "staff").get(f"/staff/cases/{filing_id}").text


def test_efile_needs_consent(env):
    r = as_(env, "client").post("/client/efile-authorization", data={"taxpayer_pin": "12345"}, follow_redirects=False)
    assert "error=" in r.headers["location"]


def test_stage_request_becomes_a_question_for_the_tax_team(env):
    client = as_(env, "client")
    client.post("/client/efile-authorization", data={"taxpayer_pin": "12345", "consent_efile": "on"})  # starts the case
    r = client.post("/client/update-stage/request", data={"request_type": "expedite", "stage_comments": "Any news?"},
                    follow_redirects=False)
    assert "thread=" in r.headers["location"]
    db = env["Session"]()
    thread = db.query(ChatThread).one()
    assert thread.kind == "QUERY" and thread.case_id == db.query(TaxFiling).one().id
    assert thread.subject.startswith("Expedited review request")
    assert db.query(ChatMessage).filter(ChatMessage.thread_id == thread.id).one().body == "Any news?"
    db.close()


def test_fbar_parts_are_saved_and_numbers_masked(env):
    client = as_(env, "client")
    filer = {"part": "filer", "filer_type": "Individual", "has_tin": "yes", "tin": "123-45-6789",
             "first_name": "Mike", "last_name": "T", "many_accounts": "no"}
    assert "saved=" in client.post("/client/fbar", data=filer, follow_redirects=False).headers["location"]
    # A blank TIN keeps the saved one
    client.post("/client/fbar", data={**filer, "tin": "", "city": "Austin"})
    account = {"part": "sep", "sep": "yes", "sep_owner": "Taxpayer", "sep_type": "Bank", "sep_institution": "HDFC Bank",
               "sep_number": "50100123456789", "sep_max": "12500.5", "sep_address": "MG Road, Hyderabad"}
    client.post("/client/fbar", data=account)
    client.post("/client/fbar", data={**account, "sep_institution": "ICICI"})
    bad = client.post("/client/fbar", data={**account, "sep_max": "lots"}, follow_redirects=False)
    assert "error=" in bad.headers["location"]
    client.post("/client/fbar", data={"part": "joint", "joint": "no"})
    client.post("/client/fbar", data={"part": "remove", "index": "1"})
    client.post("/client/fbar", data={"part": "submit", "confirm": "on"})

    row, filing_id = _section(env, intake.FBAR)
    assert row.data["filer"]["tin"] == "123456789" and row.data["filer"]["city"] == "Austin"
    assert [a["institution"] for a in row.data["accounts"]] == ["HDFC Bank"]
    assert row.data["accounts"][0]["max_value"] == "12500.50" and row.data["no_joint"] is True and row.submitted_at
    page = client.get("/client/fbar").text
    assert "Saved •••• 6789" in page and "123456789" not in page and "50100123456789" not in page
    staff_page = as_(env, "staff").get(f"/staff/cases/{filing_id}").text
    assert "HDFC Bank" in staff_page and "123456789" not in staff_page and "said they have none" in staff_page


def test_fbar_submit_needs_part_one(env):
    r = as_(env, "client").post("/client/fbar", data={"part": "submit", "confirm": "on"}, follow_redirects=False)
    assert "error=" in r.headers["location"] and r.headers["location"].endswith("#part1")


def test_residency_and_dependents_are_saved_per_year(env):
    client = as_(env, "client")
    r = client.post("/client/profile/residency", data={"nationality": "Indian", "visa_count": "2", "visa_type": "H1B",
                                                       "first_entry": "2019-08-01", "days_183": "yes"}, follow_redirects=False)
    assert r.headers["location"].endswith("#dependents")
    client.post("/client/profile/dependents", data={"dep_first": "Anya", "dep_last": "T", "dep_rel": "Daughter",
                                                    "dep_dob": "2020-01-02", "dep_id": "ssn", "dep_months": "12"})
    client.post("/client/profile/dependents", data={"dep_first": "Ravi", "dep_last": "T", "dep_rel": "Son"})
    assert "error=" in client.post("/client/profile/dependents", data={"dep_first": "X", "dep_last": "Y", "dep_months": "13"},
                                   follow_redirects=False).headers["location"]
    client.post("/client/profile/dependents", data={"action": "remove", "index": "1"})

    residency, filing_id = _section(env, intake.RESIDENCY)
    dependents, _ = _section(env, intake.DEPENDENTS)
    assert residency.data["visa_type"] == "H1B" and residency.data["visa_count"] == "2"
    assert [d["first_name"] for d in dependents.data["items"]] == ["Anya"]
    page = client.get("/client/profile").text
    assert "Anya" in page and 'value="2019-08-01"' in page
    staff_page = as_(env, "staff").get(f"/staff/cases/{filing_id}").text
    assert "H1B" in staff_page and "Anya" in staff_page


def test_no_ui_only_forms_are_left(env):
    from pathlib import Path
    import re
    live = [f for f in Path("app/modules/client/templates").rglob("*.html")
            if re.search(r"<form[^>]*data-cx-demo", re.sub(r"\{#.*?#\}", "", f.read_text(encoding="utf-8"), flags=re.S))]
    assert live == []


def test_pages_open_before_anything_is_saved(env):
    client = as_(env, "client")
    for url in ("/client/fbar", "/client/profile", "/client/efile-authorization", "/client/update-stage"):
        assert client.get(url).status_code == 200, url
    assert "Awaiting signature" in client.get("/client/efile-authorization").text
