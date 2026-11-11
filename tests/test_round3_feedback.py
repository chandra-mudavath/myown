"""Round 3 demo feedback (Nov 2026): advance payments, reset after 5 wrong passwords, staff client view and Comments."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient

from app.core.security import create_access_token
from app.main import app
from app.modules.admin.services import billing
from app.modules.staff.models.staff import Staff
from app.platform.models.auth import AccountType, AuthAccount
from app.platform.models.billing import Invoice, Payment, Service
from app.platform.models.case_workflow import CaseComment
from app.platform.models.tax_filing import TaxFiling

from tests.test_access_rules import _login
from tests.test_auth_email import PASSWORD, env as auth_env  # noqa: F401
from tests.test_payments import as_, env  # noqa: F401


def _advance(client: TestClient, amount: str = "100", ref: str = "ADV-UTR-1001"):
    return client.post("/client/payments/manual", data={"payment_method": "upi", "transaction_reference": ref,
                                                        "pay_amount": amount, "sent_amount": "8400", "sent_currency": "INR"},
                       follow_redirects=False)


def test_pay_at_any_stage_without_an_invoice(env):
    other, admin = as_(env, "other"), as_(env, "admin")
    page = other.get("/client/make-payment").text
    assert "No invoice yet" in page and 'name="pay_amount"' in page and 'placeholder="Enter amount, e.g. 150.00"' in page
    assert "No payment is due yet" not in page

    assert "saved=" in _advance(other).headers["location"]
    db = env["Session"]()
    pay = db.query(Payment).filter(Payment.client_id == env["other"]["person"].id).one()
    filing = db.get(TaxFiling, pay.case_id)
    assert pay.invoice_id is None and filing.client_id == env["other"]["person"].id and pay.status == "pending"

    listing = admin.get("/admin/payments").text
    assert "Advance payment" in listing and f"/admin/payments/payments/{pay.id}/verify" in listing
    r = admin.post(f"/admin/payments/payments/{pay.id}/verify", follow_redirects=False)
    assert r.headers["location"].startswith("/admin/payments?saved=")
    db.expire_all()
    assert db.get(Payment, pay.id).status == "succeeded"

    # The invoice issued later counts the advance: $150 fee - $100 paid = $50 left
    svc = db.query(Service).one()
    inv, err = billing.create_invoice(db, env["admin"]["account"].id, filing.id, {svc.id: "1"}, "0", "0", date.today().isoformat(), "")
    assert not err
    assert billing.issue_invoice(db, env["admin"]["account"].id, inv) is None
    db.expire_all()
    assert db.get(Payment, pay.id).invoice_id == inv.id
    assert db.get(Invoice, inv.id).status == "partially_paid"
    db.close()


def test_advance_amount_is_checked(env):
    other = as_(env, "other")
    assert "error=" in _advance(other, amount="0").headers["location"]
    assert "error=" in _advance(other, amount="abc").headers["location"]


def test_five_wrong_passwords_ask_for_a_reset(auth_env):  # noqa: F811
    for _ in range(4):
        assert "Invalid email or password" in _login("5.5.5.5", "Wrong!pass1").text
    page = _login("5.5.5.5", "Wrong!pass1").text  # the fifth
    assert "wrong password 5 times" in page and "/auth/forgot-password?email=client%40x.test" in page
    assert "Please wait" not in page and "15 minutes" not in page
    assert "wrong password 5 times" in _login("5.5.5.5", PASSWORD).text  # waiting doesn't help; a reset does
    forgot = TestClient(app).get("/auth/forgot-password?email=client%40x.test").text
    assert 'value="client@x.test"' in forgot


def _staff(env) -> TestClient:
    db = env["Session"]()
    acc = AuthAccount(email="sam@x.test", password_hash="x", account_type=AccountType.STAFF, is_active=True, is_verified=True)
    db.add(acc)
    db.flush()
    db.add(Staff(account_id=acc.id, first_name="Sam", last_name="Staff"))
    db.commit()
    db.close()
    c = TestClient(app)
    c.cookies.update({"access_token": create_access_token("sam@x.test", "STAFF")})
    return c


def test_staff_client_view_has_the_client_tabs_update_stage_and_comments(env):
    staff = _staff(env)
    page = staff.get(f"/staff/cases/{env['filing_id']}").text
    nav = page.split('aria-label="Client information"')[1].split("</nav>")[0]
    for label in ("Dashboard", "My Filings", "Messages", "Personal Info", "Tax Notes", "Upload Documents", "Tax Summary",
                  "Make Payment", "Bank Details", "FBAR", "Review Documents", "E-File Authorization", "Download Tax Docs",
                  "Refund Status", "Email &amp; Mobile", "Refer &amp; Earn", "Update stage", "Comments"):
        assert label in nav, label
    assert "data-move-stage" in nav and page.count("data-stage-dialog") == 1
    # Case header: name, email, phone; no client number chip; payments section
    header = page.split('class="case-file"')[1].split("</header>")[0]
    assert "mailto:client@x.test" in header and "staff-id-chip" not in header
    assert 'id="sec-payments"' in page and 'id="sec-refund"' in page and "$150.00 due" in page


def test_unassigned_registered_case_explains_on_hover(env):
    db = env["Session"]()
    f = db.get(TaxFiling, env["filing_id"])
    f.status = "pending_review"
    db.commit()
    db.close()
    page = _staff(env).get(f"/staff/cases/{env['filing_id']}").text
    assert 'data-tip="Unassigned filing cannot move to next stage"' in page
    assert "Not assigned yet. An admin assigns" not in page


def test_comments_page_collects_staff_comments(env):
    staff = _staff(env)
    url = f"/staff/cases/{env['filing_id']}/comments"
    assert staff.get(url).status_code == 200
    r = staff.post(url, data={"body": "Called the client about the 1099-B."}, follow_redirects=False)
    assert r.headers["location"].endswith("comments?saved=1")
    staff.post(f"/staff/cases/{env['filing_id']}/stage", data={"to": "client_review", "comment": "Draft ready", "next": url})
    page = staff.get(url).text
    assert "Called the client about the 1099-B." in page and "Draft ready" in page and "Stage moved" in page
    db = env["Session"]()
    assert db.query(CaseComment).count() == 1
    db.close()
    assert "error=" in staff.post(url, data={"body": "  "}, follow_redirects=False).headers["location"]


def test_notifications_have_a_search_box(env):
    page = _staff(env).get("/staff/notifications").text
    assert "data-notif-search" in page and "data-search=" in page
