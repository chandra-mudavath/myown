"""Client payments (UPI / bank transfer submitted for verification), against an in-memory SQLite database."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

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
from app.modules.admin.services import billing
from app.modules.client.models.client import Client
from app.platform.models.auth import AccountType, AuthAccount
from app.platform.models.billing import Invoice, Payment, Service
from app.platform.models.lookups import AccountTypeLookup, CaseStage, TaxYear
from app.platform.models.tax_filing import TaxFiling


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "PAYMENT_PROOFS_DIR", str(tmp_path / "proofs"))
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
        CaseStage(id=2, code="payment_pending", name="Payment Pending", client_status="In Progress", sort_order=2),
        CaseStage(id=3, code="client_review", name="Client Review", client_status="In Progress", sort_order=3),
        TaxYear(year=2025, name="2025", is_open=True, is_default=True, is_active=True, sort_order=1),
        Service(service_code="FED", name="Federal return", base_price=Decimal("150.00")),
    ])
    people = {}

    def account(key, kind, model):
        acc = AuthAccount(email=f"{key}@x.test", password_hash="x", account_type=kind, is_active=True, is_verified=True)
        db.add(acc)
        db.flush()
        person = model(account_id=acc.id, first_name=key.title(), last_name="T", **({"phone": "1"} if model is Client else {}))
        db.add(person)
        db.flush()
        people[key] = {"account": acc, "person": person, "cookie": {"access_token": create_access_token(acc.email, kind.value)}}

    account("client", AccountType.CLIENT, Client)
    account("other", AccountType.CLIENT, Client)
    account("admin", AccountType.ADMIN, Admin)
    filing = TaxFiling(client_id=people["client"]["person"].id, tax_year=2025, filing_type="individual",
                       first_name="Client", last_name="T", email="client@x.test", status="payment_pending")
    db.add(filing)
    db.commit()
    svc = db.query(Service).one()
    inv, err = billing.create_invoice(db, people["admin"]["account"].id, filing.id, {svc.id: "1"}, "0", "0",
                                      date.today().isoformat(), "")
    assert not err
    billing.issue_invoice(db, people["admin"]["account"].id, inv)
    people.update(Session=Session, filing_id=filing.id, invoice_id=inv.id)
    yield people
    app.dependency_overrides.clear()
    db.close()


def as_(env, key) -> TestClient:
    c = TestClient(app)
    c.cookies.update({**env[key]["cookie"], "tax_year": "2025"})
    return c


def submit(client: TestClient, invoice_id: str, ref: str = "UTR412345678901", **extra):
    data = {"invoice_id": invoice_id, "payment_method": "upi", "transaction_reference": ref,
            "sent_amount": "12500", "sent_currency": "INR", **extra}
    return client.post("/client/payments/manual", data=data, follow_redirects=False)


def test_client_sees_amount_due(env):
    page = as_(env, "client").get("/client/make-payment")
    assert page.status_code == 200
    # Make Payment is its own page: Pay in USD or Pay in INR, and the client types the amount (blank, up to the balance).
    assert 'name="pay_option" value="usd"' in page.text and 'name="pay_option" value="inr"' in page.text
    assert 'name="pay_amount"' in page.text and 'max="150.00"' in page.text and 'value="150.00"' not in page.text
    assert 'action="/client/payments/manual"' in page.text


def test_partial_payment_leaves_the_rest_due(env):
    client, admin = as_(env, "client"), as_(env, "admin")
    assert "error=" in submit(client, env["invoice_id"], pay_amount="150.01").headers["location"]  # more than due
    assert "error=" in submit(client, env["invoice_id"], pay_amount="0").headers["location"]
    assert "saved=" in submit(client, env["invoice_id"], pay_amount="100").headers["location"]
    db = env["Session"]()
    pay = db.query(Payment).filter(Payment.status == "pending").one()
    assert pay.amount == Decimal("100.00")
    admin.post(f"/admin/payments/payments/{pay.id}/verify", follow_redirects=False)
    db.expire_all()
    assert db.get(Invoice, env["invoice_id"]).status == "partially_paid"
    assert db.get(TaxFiling, env["filing_id"]).status == "payment_pending"  # not paid in full yet
    assert 'max="50.00"' in client.get("/client/make-payment").text
    db.close()


def test_submit_reject_resubmit_verify(env):
    client, admin = as_(env, "client"), as_(env, "admin")
    r = client.post("/client/payments/manual", data={"invoice_id": env["invoice_id"], "payment_method": "bank_transfer",
                                                     "transaction_reference": "FED123456", "sent_amount": "150",
                                                     "sent_currency": "USD"},
                    files={"proof": ("slip.png", b"\x89PNG data", "image/png")}, follow_redirects=False)
    assert r.status_code == 303 and "saved=" in r.headers["location"]
    db = env["Session"]()
    pay = db.query(Payment).one()
    assert pay.status == "pending" and pay.amount == Decimal("150.00") and pay.proof_path
    assert db.get(Invoice, env["invoice_id"]).status == "issued"  # pending doesn't count yet

    assert "error=" in submit(client, env["invoice_id"]).headers["location"]  # one pending at a time
    assert admin.post(f"/admin/payments/invoices/{env['invoice_id']}/void", follow_redirects=False).headers["location"].count("error=")
    assert admin.get(f"/admin/payments/payments/{pay.id}/proof").content == b"\x89PNG data"
    assert "Awaiting verification" in admin.get("/admin/payments").text

    admin.post(f"/admin/payments/payments/{pay.id}/reject", data={"reason": "No payment found"}, follow_redirects=False)
    db.expire_all()
    assert db.get(Payment, pay.id).status == "failed"
    assert "No payment found" in client.get("/client/tax-summary").text

    assert "saved=" in submit(client, env["invoice_id"]).headers["location"]
    pay2 = db.query(Payment).filter(Payment.status == "pending").one()
    assert pay2.original_amount == Decimal("12500.00") and pay2.original_currency == "INR"
    admin.post(f"/admin/payments/payments/{pay2.id}/verify", follow_redirects=False)
    db.expire_all()
    assert db.get(Payment, pay2.id).status == "succeeded"
    assert db.get(Invoice, env["invoice_id"]).status == "paid"
    assert db.get(TaxFiling, env["filing_id"]).status == "client_review"
    db.close()


def test_client_cannot_pay_someone_elses_invoice(env):
    r = submit(as_(env, "other"), env["invoice_id"])
    assert "error=" in r.headers["location"]
    db = env["Session"]()
    assert db.query(Payment).count() == 0
    db.close()


def test_rejects_bad_input(env):
    client = as_(env, "client")
    assert "error=" in submit(client, env["invoice_id"], ref="12").headers["location"]
    assert "error=" in submit(client, env["invoice_id"], sent_amount="abc").headers["location"]
    assert "error=" in submit(client, env["invoice_id"], payment_method="card").headers["location"]
    bad_proof = client.post("/client/payments/manual", data={"invoice_id": env["invoice_id"], "payment_method": "upi",
                                                             "transaction_reference": "UTR99999", "sent_amount": "1",
                                                             "sent_currency": "INR"},
                            files={"proof": ("x.exe", b"MZ", "application/octet-stream")}, follow_redirects=False)
    assert "error=" in bad_proof.headers["location"]


# ── PayPal (the PayPal API is faked) ──────────────────────────────────────────

class FakePayPal:
    def __init__(self):
        self.orders, self.captured, self.capture_status = {}, [], "COMPLETED"

    def create_order(self, *, amount, custom_id, **_):
        order_id = f"ORDER{len(self.orders) + 1}"
        self.orders[order_id] = {"amount": amount, "custom_id": custom_id}
        return order_id, f"https://paypal.test/approve/{order_id}"

    def capture_order(self, order_id, request_id):
        self.captured.append(order_id)
        o = self.orders[order_id]
        return {"purchase_units": [{"payments": {"captures": [
            {"id": f"CAP-{order_id}", "status": self.capture_status, "custom_id": o["custom_id"],
             "amount": {"value": f"{o['amount']:.2f}", "currency_code": "USD"}}]}}]}


@pytest.fixture()
def fake_paypal(monkeypatch):
    from app.platform.services import paypal
    fake = FakePayPal()
    monkeypatch.setattr(settings, "PAYPAL_CLIENT_ID", "id")
    monkeypatch.setattr(settings, "PAYPAL_CLIENT_SECRET", "secret")
    monkeypatch.setattr(paypal, "create_order", fake.create_order)
    monkeypatch.setattr(paypal, "capture_order", fake.capture_order)
    return fake


def test_paypal_is_offered_under_pay_in_us_when_configured(env, fake_paypal):
    page = as_(env, "client").get("/client/make-payment").text
    # Pay in USD → Pay now → the "Complete your payment" window posts to PayPal's checkout
    assert "data-pay-open" in page and 'action="/client/payments/paypal/start"' in page and "Continue to payment" in page


def test_paypal_checkout_capture(env, fake_paypal):
    client = as_(env, "client")
    r = client.post("/client/payments/paypal/start", data={"invoice_id": env["invoice_id"]}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "https://paypal.test/approve/ORDER1"
    # walked away and started again: the first checkout is replaced
    client.post("/client/payments/paypal/start", data={"invoice_id": env["invoice_id"]}, follow_redirects=False)
    r = client.get("/client/payments/paypal/return?token=ORDER2", follow_redirects=False)
    assert "saved=" in r.headers["location"]
    client.get("/client/payments/paypal/return?token=ORDER2", follow_redirects=False)  # refresh: no second capture
    assert fake_paypal.captured == ["ORDER2"]
    db = env["Session"]()
    pay = db.query(Payment).filter(Payment.gateway_payment_id == "ORDER2").one()
    assert pay.status == "succeeded" and pay.transaction_reference == "CAP-ORDER2" and pay.amount == Decimal("150.00")
    assert db.get(Invoice, env["invoice_id"]).status == "paid"
    assert db.get(TaxFiling, env["filing_id"]).status == "client_review"
    # approving the old order now must not charge again
    assert "error=" in client.get("/client/payments/paypal/return?token=ORDER1", follow_redirects=False).headers["location"]
    assert fake_paypal.captured == ["ORDER2"]
    db.close()


def test_paypal_cancel_and_other_clients(env, fake_paypal):
    client, other = as_(env, "client"), as_(env, "other")
    assert "error=" in other.post("/client/payments/paypal/start", data={"invoice_id": env["invoice_id"]},
                                  follow_redirects=False).headers["location"]
    client.post("/client/payments/paypal/start", data={"invoice_id": env["invoice_id"]}, follow_redirects=False)
    assert "error=" in other.get("/client/payments/paypal/return?token=ORDER1", follow_redirects=False).headers["location"]
    assert fake_paypal.captured == []
    client.get("/client/payments/paypal/cancel?token=ORDER1", follow_redirects=False)
    db = env["Session"]()
    assert db.query(Payment).one().status == "failed"
    db.close()


def test_no_webhook_endpoint(env):
    assert TestClient(app).post("/webhooks/paypal", json={}).status_code == 404


def test_paypal_pending_capture_is_verified_by_admin(env, fake_paypal):
    """Without webhooks, a capture PayPal leaves pending is checked in PayPal and verified by an admin."""
    client, admin = as_(env, "client"), as_(env, "admin")
    fake_paypal.capture_status = "PENDING"
    client.post("/client/payments/paypal/start", data={"invoice_id": env["invoice_id"]}, follow_redirects=False)
    client.get("/client/payments/paypal/return?token=ORDER1", follow_redirects=False)
    db = env["Session"]()
    pay = db.query(Payment).one()
    assert pay.status == "pending" and pay.transaction_reference == "CAP-ORDER1"
    assert db.get(Invoice, env["invoice_id"]).status == "issued"
    assert "Awaiting verification" in admin.get("/admin/payments").text
    admin.post(f"/admin/payments/payments/{pay.id}/verify", follow_redirects=False)
    db.expire_all()
    assert db.get(Payment, pay.id).status == "succeeded" and db.get(Invoice, env["invoice_id"]).status == "paid"
    db.close()


def test_unfinished_paypal_checkout_is_not_reviewable(env, fake_paypal):
    client, admin = as_(env, "client"), as_(env, "admin")
    client.post("/client/payments/paypal/start", data={"invoice_id": env["invoice_id"]}, follow_redirects=False)
    db = env["Session"]()
    pay = db.query(Payment).one()
    assert "error=" in admin.post(f"/admin/payments/payments/{pay.id}/verify", follow_redirects=False).headers["location"]
    db.expire_all()
    assert db.get(Payment, pay.id).status == "pending"
    db.close()
