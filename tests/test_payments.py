"""Client payments (UPI / bank transfer submitted for verification, Stripe Checkout and its webhooks), against an
in-memory SQLite database."""
from __future__ import annotations

import hashlib
import hmac
import json
import time

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

    # No "one pending at a time" rule any more (client feedback, Nov 2026), but a reference can't be used twice
    assert "error=" in submit(client, env["invoice_id"], ref="FED123456").headers["location"]
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


# ── Stripe (the Stripe API is faked; webhooks are signed for real) ────────────

class FakeStripe:
    """Checkout sessions in memory. `state[session_id]` is what Stripe would report: open / paid / processing /
    failed / expired."""

    def __init__(self):
        self.sessions, self.state, self.refunds = {}, {}, []

    def create_checkout_session(self, *, amount, payment_id, success_url, cancel_url, **_):
        sid = f"cs_test_{len(self.sessions) + 1}"
        self.sessions[sid] = {"amount": amount, "payment_id": payment_id, "cancel_url": cancel_url}
        self.state[sid] = "open"
        return sid, f"https://checkout.stripe.test/{sid}"

    def get_checkout_session(self, sid):
        state, amount = self.state[sid], self.sessions[sid]["amount"]
        session = {"id": sid, "amount_total": int(amount * 100), "currency": "usd",
                   "status": {"open": "open", "expired": "expired"}.get(state, "complete"),
                   "payment_status": "paid" if state == "paid" else "unpaid"}
        if session["status"] == "complete":
            ach = state in ("processing", "failed")
            session["payment_intent"] = {
                "id": f"pi_{sid}", "status": {"paid": "succeeded", "processing": "processing"}.get(state, "requires_payment_method"),
                "last_payment_error": {"message": "The bank account has insufficient funds."} if state == "failed" else None,
                "latest_charge": {"id": f"ch_{sid}", "payment_method_details": {"type": "us_bank_account" if ach else "card"},
                                  "balance_transaction": {"fee": 465} if state == "paid" else None}}
        return session

    def expire_checkout_session(self, sid):
        from app.platform.services.stripe_api import StripeError
        if self.state[sid] != "open":
            raise StripeError("not open", "checkout_session_not_open", 400)
        self.state[sid] = "expired"
        return {"id": sid, "status": "expired"}

    def create_refund(self, payment_intent_id, amount, idempotency_key, reason="requested_by_customer"):
        self.refunds.append((payment_intent_id, amount))
        return {"id": f"re_{len(self.refunds)}", "status": "succeeded"}


WHSEC = "whsec_test_secret"


@pytest.fixture()
def fake_stripe(monkeypatch):
    from app.platform.services import stripe_api
    fake = FakeStripe()
    monkeypatch.setattr(settings, "STRIPE_SECRET_KEY", "sk_test_x")
    monkeypatch.setattr(settings, "STRIPE_WEBHOOK_SECRET", WHSEC)
    for name in ("create_checkout_session", "get_checkout_session", "expire_checkout_session", "create_refund"):
        monkeypatch.setattr(stripe_api, name, getattr(fake, name))
    return fake


def send_event(event: dict, secret: str = WHSEC, age: int = 0):
    body = json.dumps(event).encode()
    t = int(time.time()) - age
    sig = hmac.new(secret.encode(), f"{t}.".encode() + body, hashlib.sha256).hexdigest()
    return TestClient(app).post("/webhooks/stripe", content=body,
                                headers={"Stripe-Signature": f"t={t},v1={sig}", "Content-Type": "application/json"})


def start(client: TestClient, invoice_id: str, **extra):
    return client.post("/client/payments/stripe/start", data={"invoice_id": invoice_id, **extra}, follow_redirects=False)


def completed(sid: str, event_id: str = "evt_1", kind: str = "checkout.session.completed") -> dict:
    return {"id": event_id, "type": kind, "data": {"object": {"id": sid}}}


def test_stripe_is_offered_under_pay_in_usd_when_configured(env, fake_stripe):
    page = as_(env, "client").get("/client/make-payment").text
    # Pay in USD → Pay now → the "Complete your payment" window posts to Stripe Checkout
    assert "data-pay-open" in page and 'action="/client/payments/stripe/start"' in page and "Secured by Stripe" in page
    assert "Card payment isn't switched on yet" not in page


def test_stripe_off_without_keys(env):
    client = as_(env, "client")
    assert "Card payment isn't switched on yet" in client.get("/client/make-payment").text
    assert "error=" in start(client, env["invoice_id"]).headers["location"]


def test_stripe_checkout_paid(env, fake_stripe):
    client = as_(env, "client")
    r = start(client, env["invoice_id"])
    assert r.status_code == 303 and r.headers["location"] == "https://checkout.stripe.test/cs_test_1"
    # walked away and started again: the first page is closed at Stripe so it can't be paid any more
    start(client, env["invoice_id"])
    assert fake_stripe.state["cs_test_1"] == "expired"
    fake_stripe.state["cs_test_2"] = "paid"
    r = client.get("/client/payments/stripe/return?session_id=cs_test_2", follow_redirects=False)
    assert "saved=" in r.headers["location"]
    client.get("/client/payments/stripe/return?session_id=cs_test_2", follow_redirects=False)  # refresh: no change
    db = env["Session"]()
    pay = db.query(Payment).filter(Payment.gateway_payment_id == "cs_test_2").one()
    assert pay.status == "succeeded" and pay.transaction_reference == "pi_cs_test_2" and pay.payment_method == "card"
    assert pay.amount == Decimal("150.00") and pay.fee_amount == Decimal("4.65")
    assert db.query(Payment).filter(Payment.gateway_payment_id == "cs_test_1").one().status == "failed"
    assert db.get(Invoice, env["invoice_id"]).status == "paid"
    assert db.get(TaxFiling, env["filing_id"]).status == "client_review"
    db.close()
    assert "Card / Apple Pay / Google Pay" in client.get("/client/tax-summary").text


def test_stripe_cancel_and_other_clients(env, fake_stripe):
    client, other = as_(env, "client"), as_(env, "other")
    assert "error=" in start(other, env["invoice_id"]).headers["location"]
    start(client, env["invoice_id"])
    fake_stripe.state["cs_test_1"] = "paid"
    assert "error=" in other.get("/client/payments/stripe/return?session_id=cs_test_1",
                                 follow_redirects=False).headers["location"]
    db = env["Session"]()
    pay = db.query(Payment).one()
    assert pay.status == "pending"
    fake_stripe.state["cs_test_1"] = "open"
    other.get(f"/client/payments/stripe/cancel?payment={pay.id}", follow_redirects=False)  # not theirs: ignored
    assert fake_stripe.state["cs_test_1"] == "open"
    client.get(f"/client/payments/stripe/cancel?payment={pay.id}", follow_redirects=False)
    db.expire_all()
    assert db.get(Payment, pay.id).status == "failed" and fake_stripe.state["cs_test_1"] == "expired"
    db.close()
    assert "Not accepted" not in client.get("/client/tax-summary").text  # an unfinished checkout isn't listed


def test_webhook_signature_is_checked(env, fake_stripe):
    event = completed("cs_test_1")
    assert send_event(event, secret="whsec_wrong").status_code == 400
    assert send_event(event, age=3600).status_code == 400  # replayed an hour later
    assert TestClient(app).post("/webhooks/stripe", json=event).status_code == 400  # unsigned
    assert send_event(event).json()["result"] == "ignored"  # signed, but not one of our payments
    assert TestClient(app).post("/webhooks/paypal", json={}).status_code == 404  # PayPal is gone


def test_ach_clears_through_webhooks(env, fake_stripe):
    client, admin = as_(env, "client"), as_(env, "admin")
    start(client, env["invoice_id"])
    fake_stripe.state["cs_test_1"] = "processing"  # an ACH debit: Checkout completes, the money clears days later
    assert send_event(completed("cs_test_1")).json()["result"] == "applied"
    db = env["Session"]()
    pay = db.query(Payment).one()
    assert pay.status == "pending" and pay.payment_method == "ach" and pay.transaction_reference == "pi_cs_test_1"
    assert db.get(Invoice, env["invoice_id"]).status == "issued"
    assert "Processing at your bank" in client.get("/client/tax-summary").text
    from app.platform.services import payments as client_payments
    assert client_payments.pending_payments(db) == []  # Stripe confirms it, not an admin
    assert "error=" in admin.post(f"/admin/payments/payments/{pay.id}/verify", follow_redirects=False).headers["location"]

    fake_stripe.state["cs_test_1"] = "paid"
    paid = completed("cs_test_1", "evt_2", "checkout.session.async_payment_succeeded")
    assert send_event(paid).json()["result"] == "applied"
    assert send_event(paid).json()["result"] == "duplicate"  # Stripe redelivers: applied once
    db.expire_all()
    assert db.get(Payment, pay.id).status == "succeeded" and db.get(Invoice, env["invoice_id"]).status == "paid"
    db.close()


def test_ach_failure(env, fake_stripe):
    client = as_(env, "client")
    start(client, env["invoice_id"])
    fake_stripe.state["cs_test_1"] = "failed"
    send_event(completed("cs_test_1", kind="checkout.session.async_payment_failed"))
    db = env["Session"]()
    pay = db.query(Payment).one()
    assert pay.status == "failed" and "insufficient funds" in pay.failure_reason
    db.close()
    assert "insufficient funds" in client.get("/client/tax-summary").text


def test_admin_refund_and_dispute(env, fake_stripe):
    client, admin = as_(env, "client"), as_(env, "admin")
    start(client, env["invoice_id"])
    fake_stripe.state["cs_test_1"] = "paid"
    client.get("/client/payments/stripe/return?session_id=cs_test_1", follow_redirects=False)
    db = env["Session"]()
    pay = db.query(Payment).one()
    assert "Refund payment" in admin.get(f"/admin/payments/invoices/{env['invoice_id']}").text
    assert "error=" in admin.post(f"/admin/payments/payments/{pay.id}/refund", data={"amount": "200"},
                                  follow_redirects=False).headers["location"]
    assert "saved=" in admin.post(f"/admin/payments/payments/{pay.id}/refund", data={"amount": "50"},
                                  follow_redirects=False).headers["location"]
    assert fake_stripe.refunds == [("pi_cs_test_1", Decimal("50.00"))]
    db.expire_all()
    pay = db.get(Payment, pay.id)
    assert pay.status == "succeeded" and pay.refunded_amount == Decimal("50.00")
    assert db.get(Invoice, env["invoice_id"]).status == "partially_paid"  # $100 kept of $150
    assert "$50.00 refunded" in client.get("/client/tax-summary").text
    # Stripe's own charge.refunded for that same refund changes nothing
    send_event({"id": "evt_r1", "type": "charge.refunded",
                "data": {"object": {"payment_intent": "pi_cs_test_1", "amount_refunded": 5000}}})
    db.expire_all()
    assert db.get(Payment, pay.id).refunded_amount == Decimal("50.00")
    send_event({"id": "evt_d1", "type": "charge.dispute.created",
                "data": {"object": {"id": "dp_1", "payment_intent": "pi_cs_test_1", "status": "needs_response"}}})
    db.expire_all()
    assert db.get(Payment, pay.id).dispute_status == "needs_response"
    send_event({"id": "evt_d2", "type": "charge.dispute.closed",
                "data": {"object": {"id": "dp_1", "payment_intent": "pi_cs_test_1", "status": "lost"}}})
    db.expire_all()
    pay = db.get(Payment, pay.id)
    assert pay.status == "refunded" and pay.refunded_amount == Decimal("150.00")
    assert db.get(Invoice, env["invoice_id"]).status == "issued"
    db.close()


def test_admin_check_with_stripe(env, fake_stripe):
    """No webhook arrived and the client never came back: an admin reads the payment from Stripe."""
    client, admin = as_(env, "client"), as_(env, "admin")
    start(client, env["invoice_id"])
    fake_stripe.state["cs_test_1"] = "paid"
    db = env["Session"]()
    pay = db.query(Payment).one()
    assert "Check with Stripe" in admin.get(f"/admin/payments/invoices/{env['invoice_id']}").text
    assert "saved=" in admin.post(f"/admin/payments/payments/{pay.id}/sync", follow_redirects=False).headers["location"]
    db.expire_all()
    assert db.get(Payment, pay.id).status == "succeeded" and db.get(Invoice, env["invoice_id"]).status == "paid"
    db.close()


def test_advance_payment_by_card(env, fake_stripe):
    """No open invoice: the client pays any amount, kept on the case until an invoice is issued."""
    db = env["Session"]()
    db.get(Invoice, env["invoice_id"]).status = "void"
    db.commit()
    client = as_(env, "client")
    assert start(client, "", pay_amount="75").status_code == 303
    fake_stripe.state["cs_test_1"] = "paid"
    client.get("/client/payments/stripe/return?session_id=cs_test_1", follow_redirects=False)
    pay = db.query(Payment).one()
    assert pay.status == "succeeded" and pay.invoice_id is None and pay.amount == Decimal("75.00")
    db.close()


def test_verify_event_signature():
    from app.platform.services import stripe_api
    settings_secret = settings.STRIPE_WEBHOOK_SECRET
    try:
        settings.STRIPE_WEBHOOK_SECRET = WHSEC
        body, t = b'{"id": "evt_1"}', 1_700_000_000
        sig = hmac.new(WHSEC.encode(), f"{t}.".encode() + body, hashlib.sha256).hexdigest()
        assert stripe_api.verify_event(body, f"t={t},v1=bad,v1={sig}", now=t + 10)["id"] == "evt_1"  # any v1 may match
        with pytest.raises(stripe_api.StripeError):
            stripe_api.verify_event(body + b" ", f"t={t},v1={sig}", now=t)  # body changed
    finally:
        settings.STRIPE_WEBHOOK_SECRET = settings_secret
