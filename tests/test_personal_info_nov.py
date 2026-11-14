"""Client feedback (Nov 2026): other nationality / visa and earlier visas, marital status moved to Personal Info,
dependents' SSN / ITIN numbers, password eye on sign-up, Yes highlight, refund cards, email change with admin approval."""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.core.security import create_access_token
from app.main import app
from app.modules.admin.models.admin import Admin
from app.platform.models.auth import AccountType, AuthAccount, EmailChangeRequest
from app.platform.models.tax_filing import TaxFiling
from app.platform.services import intake

from tests.test_client_forms import _section, as_, env  # noqa: F401


def test_other_nationality_and_visa_and_earlier_visas(env):
    client = as_(env, "client")
    base = {"visa_count": "3", "nationality": "Other", "visa_type": "Other", "days_183": "yes"}
    assert "Enter+your+nationality" in client.post("/client/profile/residency", data=base, follow_redirects=False).headers["location"]
    base["nationality_other"] = "Canadian"
    assert "current+visa" in client.post("/client/profile/residency", data=base, follow_redirects=False).headers["location"]
    base["visa_type_other"] = "TN"
    assert "earlier+visa+1" in client.post("/client/profile/residency", data=base, follow_redirects=False).headers["location"]
    base.update(prev1_type="F1", prev1_from="2025-01-01", prev1_to="2025-03-31", prev2_type="Other", prev2_other="O-1")
    # Residential status (183 days) is required (client feedback, Nov 2026)
    unanswered = {k: v for k, v in base.items() if k != "days_183"}
    moved = client.post("/client/profile/residency", data=unanswered, follow_redirects=False).headers["location"]
    assert "183+days" in moved and moved.endswith("#return-type")
    assert 'name="days_183" value="yes" required' in client.get("/client/profile").text
    assert client.post("/client/profile/residency", data=base, follow_redirects=False).headers["location"].endswith("#marital")
    residency, filing_id = _section(env, intake.RESIDENCY)
    assert residency.data["nationality_other"] == "Canadian" and residency.data["visa_type_other"] == "TN"
    assert [v["type"] for v in residency.data["earlier_visas"]] == ["F1", "Other"] and residency.data["earlier_visas"][1]["other"] == "O-1"
    page = client.get("/client/profile").text
    assert 'data-cx-show-when="nationality=Other"' in page and 'data-cx-show-when="visa_count=2|3"' in page
    staff = as_(env, "staff").get(f"/staff/cases/{filing_id}").text
    assert "Canadian" in staff and "TN" in staff and "Earlier visa 2: O-1" in staff


def test_marital_status_saves_spouse_and_masks_the_ssn(env):
    client = as_(env, "client")
    assert "Are you married?" not in client.get("/client/tax-notes").text  # moved to Personal Info
    form = {"married": "yes", "filing": "jointly", "spouse_first": "Priya", "spouse_last": "T", "spouse_dob": "1990-05-04",
            "spouse_id": "ssn"}
    assert "9-digit+SSN" in client.post("/client/profile/marital", data=form, follow_redirects=False).headers["location"]
    form["spouse_id_number"] = "987-65-4321"
    assert client.post("/client/profile/marital", data=form, follow_redirects=False).headers["location"].endswith("#dependents")
    section, filing_id = _section(env, intake.SPOUSE)
    assert section.data["spouse"]["id_number"] == "987654321"
    db = env["Session"]()
    f = db.get(TaxFiling, filing_id)
    assert f.filing_status == "married_jointly" and f.spouse_first_name == "Priya" and str(f.spouse_date_of_birth) == "1990-05-04"
    db.close()
    page = client.get("/client/profile").text
    assert "987654321" not in page and "987-65-4321" not in page and "4321" in page  # masked only
    # Saving again without retyping keeps the number
    form.pop("spouse_id_number")
    client.post("/client/profile/marital", data=form)
    assert _section(env, intake.SPOUSE)[0].data["spouse"]["id_number"] == "987654321"
    staff = as_(env, "staff").get(f"/staff/cases/{filing_id}").text
    assert "Priya" in staff and "SSN •••• 4321" in staff and "987654321" not in staff
    # Not married clears the spouse
    client.post("/client/profile/marital", data={"married": "no"})
    db = env["Session"]()
    f = db.get(TaxFiling, filing_id)
    assert f.filing_status == "single" and f.spouse_first_name is None
    db.close()


def test_dependent_ssn_or_itin_number_is_required(env):
    client = as_(env, "client")
    r = client.post("/client/profile/dependents", data={"dep_first": "Anya", "dep_last": "T", "dep_id": "itin"}, follow_redirects=False)
    assert "9-digit+ITIN" in r.headers["location"]
    client.post("/client/profile/dependents", data={"dep_first": "Anya", "dep_last": "T", "dep_id": "itin", "dep_id_number": "912345678"})
    client.post("/client/profile/dependents", data={"dep_first": "Ravi", "dep_last": "T", "dep_id": "applying"})
    deps, filing_id = _section(env, intake.DEPENDENTS)
    assert [(d["id_type"], d["id_number"]) for d in deps.data["items"]] == [("itin", "912345678"), ("applying", "")]
    page = client.get("/client/profile").text
    assert "912345678" not in page and "ITIN •••• 5678" in page and "Applying for an ID" in page
    assert "ITIN •••• 5678" in as_(env, "staff").get(f"/staff/cases/{filing_id}").text


def test_sign_up_has_password_eyes():
    page = TestClient(app).get("/auth/register").text
    assert 'data-password-toggle="password"' in page and 'data-password-toggle="password_confirmation"' in page


def test_yes_is_highlighted_and_refund_cards_hidden(env):
    css = Path("app/modules/client/static/css/client.css").read_text(encoding="utf-8")
    assert '.cx-seg__opt input[value="yes"]:checked + span { background: var(--cx-brand); color: #fff;' in css
    page = as_(env, "client").get("/client/refund-request").text
    for title in ("State tax agencies", "Treasury Offset Program", "Taxpayer Advocate Service"):
        assert f"<strong>{title}</strong>" not in page and title not in page.split("<main")[-1].split("{#")[0]


def test_email_change_needs_admin_approval(env):
    client = as_(env, "client")
    db = env["Session"]()
    db.add(AuthAccount(email="taken@fastmail.com", password_hash="x", account_type=AccountType.CLIENT, is_active=True, is_verified=True))
    db.commit()
    db.close()
    r = client.post("/client/api/email-request", json={"email": "Mike.New@fastmail.com"})
    assert r.status_code == 200 and "admin will approve" in r.json()["message"]
    assert "Waiting for approval" in client.get("/client/update-contact").text
    assert client.post("/client/api/email-request", json={"email": "taken@fastmail.com"}).status_code == 409  # taken

    db = env["Session"]()
    acc = AuthAccount(email="boss@x.test", password_hash="x", account_type=AccountType.ADMIN, is_active=True, is_verified=True)
    db.add(acc)
    db.flush()
    db.add(Admin(account_id=acc.id, first_name="Boss", last_name="A"))
    db.commit()
    req = db.query(EmailChangeRequest).filter(EmailChangeRequest.status == "pending").one()
    db.close()
    admin = TestClient(app)
    admin.cookies.update({"access_token": create_access_token("boss@x.test", "ADMIN")})
    assert "1 email change request" in admin.get("/admin/dashboard").text
    assert "mike.new@fastmail.com" in admin.get("/admin/email-requests").text
    r = admin.post(f"/admin/email-requests/{req.id}/approve", follow_redirects=False)
    assert "saved=" in r.headers["location"]
    db = env["Session"]()
    assert db.query(AuthAccount).filter(AuthAccount.email == "mike.new@fastmail.com").count() == 1
    assert db.query(AuthAccount).filter(AuthAccount.email == "client@x.test").count() == 0
    db.close()
    # Rejecting needs a reason, and the client sees it
    new = TestClient(app)
    new.cookies.update({"access_token": create_access_token("mike.new@fastmail.com", "CLIENT"), "tax_year": "2025"})
    new.post("/client/api/email-request", json={"email": "other@fastmail.com"})
    db = env["Session"]()
    req2 = db.query(EmailChangeRequest).filter(EmailChangeRequest.status == "pending").one()
    db.close()
    assert "error=" in admin.post(f"/admin/email-requests/{req2.id}/reject", data={"reason": ""}, follow_redirects=False).headers["location"]
    admin.post(f"/admin/email-requests/{req2.id}/reject", data={"reason": "Please call us to confirm."})
    assert "Please call us to confirm." in new.get("/client/update-contact").text
