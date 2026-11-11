"""Pay in INR with Razorpay (Checkout, webhooks, refunds, Smart Collect bank transfer). The Razorpay API is faked;
Checkout and webhook signatures are computed for real."""
from __future__ import annotations

import hashlib
import hmac
import json
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import app
from app.platform.models.billing import Invoice, Payment, VirtualAccount
from app.platform.models.tax_filing import TaxFiling
from tests.test_payments import as_, env  # noqa: F401  (env is a fixture)

KEY_SECRET, WEBHOOK_SECRET = "rzp_secret", "rzp_whsec"


class FakeRazorpay:
    def __init__(self):
        self.orders, self.payments, self.refunds, self.accounts = {}, {}, [], []

    def create_order(self, *, amount_inr, receipt, notes):
        oid = f"order_{len(self.orders) + 1}"
        self.orders[oid] = {"id": oid, "amount": int(amount_inr * 100), "receipt": receipt, "notes": notes}
        return self.orders[oid]

    def pay(self, order_id, status="captured", method="upi", error=None):
        """The client pays (or fails to pay) on an order; returns the payment entity."""
        pid = f"pay_{len(self.payments) + 1}"
        self.payments[pid] = {"id": pid, "entity": "payment", "order_id": order_id, "amount": self.orders[order_id]["amount"],
                              "currency": "INR", "status": status, "method": method, "fee": 31300 if status == "captured" else None,
                              "amount_refunded": 0, "error_description": error}
        return self.payments[pid]

    def get_payment(self, payment_id):
        return self.payments[payment_id]

    def order_payments(self, order_id):
        return [p for p in self.payments.values() if p["order_id"] == order_id]

    def capture_payment(self, payment_id, amount_paise):
        self.payments[payment_id]["status"] = "captured"
        return self.payments[payment_id]

    def refund_payment(self, payment_id, amount_paise, notes):
        self.refunds.append((payment_id, amount_paise))
        self.payments[payment_id]["amount_refunded"] += amount_paise
        return {"id": f"rfnd_{len(self.refunds)}", "amount": amount_paise}

    def create_virtual_account(self, *, client_id, client_name, description):
        self.accounts.append(client_id)
        return {"id": f"va_{len(self.accounts)}", "receivers": [
            {"entity": "bank_account", "account_number": "2223330001234", "ifsc": "RAZR0000001", "bank_name": "RBL Bank",
             "name": "CovrentTax"}]}


@pytest.fixture()
def rzp(monkeypatch):
    from app.platform.services import razorpay_api
    fake = FakeRazorpay()
    monkeypatch.setattr(settings, "RAZORPAY_KEY_ID", "rzp_test_key")
    monkeypatch.setattr(settings, "RAZORPAY_KEY_SECRET", KEY_SECRET)
    monkeypatch.setattr(settings, "RAZORPAY_WEBHOOK_SECRET", WEBHOOK_SECRET)
    monkeypatch.setattr(settings, "RAZORPAY_SMART_COLLECT", True)
    for name in ("create_order", "get_payment", "order_payments", "capture_payment", "refund_payment", "create_virtual_account"):
        monkeypatch.setattr(razorpay_api, name, getattr(fake, name))
    return fake


def set_rate(env, rate="88.50"):
    return as_(env, "admin").post("/admin/settings/inr-rate", data={"rate": rate}, follow_redirects=False)


def start(client, invoice_id, **extra):
    return client.post("/client/payments/razorpay/start", data={"invoice_id": invoice_id, "pay_amount": "150", **extra})


def signature(order_id, payment_id, secret=KEY_SECRET):
    return hmac.new(secret.encode(), f"{order_id}|{payment_id}".encode(), hashlib.sha256).hexdigest()


def verify(client, order_id, payment_id, sig=None):
    return client.post("/client/payments/razorpay/verify", follow_redirects=False, data={
        "razorpay_order_id": order_id, "razorpay_payment_id": payment_id,
        "razorpay_signature": sig if sig is not None else signature(order_id, payment_id)})


def send(event: dict, event_id: str, secret=WEBHOOK_SECRET):
    body = json.dumps(event).encode()
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return TestClient(app).post("/webhooks/razorpay", content=body, headers={
        "X-Razorpay-Signature": sig, "X-Razorpay-Event-Id": event_id, "Content-Type": "application/json"})


def payment_event(kind, entity):
    return {"entity": "event", "event": kind, "payload": {"payment": {"entity": entity}}}


def test_off_until_keys_and_rate(env, monkeypatch):
    client = as_(env, "client")
    assert 'action="/client/payments/manual"' in client.get("/client/make-payment").text  # no keys: the manual UPI form
    monkeypatch.setattr(settings, "RAZORPAY_KEY_ID", "rzp_test_key")
    monkeypatch.setattr(settings, "RAZORPAY_KEY_SECRET", KEY_SECRET)
    assert "Secured by Razorpay" not in client.get("/client/make-payment").text  # keys but no rate yet
    assert "Needs a rate" in as_(env, "admin").get("/admin/settings").text
    assert start(client, env["invoice_id"]).status_code == 400


def test_admin_sets_rate(env, rzp):
    assert "error=" in set_rate(env, "abc").headers["location"]
    assert "error=" in set_rate(env, "0").headers["location"]
    assert "saved=" in set_rate(env, "88.5").headers["location"]
    page = as_(env, "admin").get("/admin/settings").text
    assert 'value="88.50"' in page and "₹13,275.00" in page
    client_page = as_(env, "client").get("/client/make-payment").text
    assert "Secured by Razorpay" in client_page and 'data-rate="88.5000"' in client_page
    assert 'action="/client/payments/manual"' not in client_page


def test_checkout_paid(env, rzp):
    set_rate(env)
    client = as_(env, "client")
    r = start(client, env["invoice_id"])
    order = r.json()
    assert r.status_code == 200 and order["order_id"] == "order_1" and order["amount"] == 1327500 and order["key"] == "rzp_test_key"
    db = env["Session"]()
    pay = db.query(Payment).one()
    assert pay.amount == Decimal("150.00") and pay.original_amount == Decimal("13275.00") and pay.original_currency == "INR"

    rp = rzp.pay("order_1")
    assert "error=" in verify(client, "order_1", rp["id"], sig="forged").headers["location"]
    assert "error=" in verify(as_(env, "other"), "order_1", rp["id"]).headers["location"]  # someone else's payment
    assert "saved=" in verify(client, "order_1", rp["id"]).headers["location"]
    assert "saved=" in verify(client, "order_1", rp["id"]).headers["location"]  # refresh: no change
    db.expire_all()
    pay = db.get(Payment, pay.id)
    assert pay.status == "succeeded" and pay.transaction_reference == rp["id"] and pay.payment_method == "upi"
    assert pay.fee_amount == Decimal("313.00")
    assert db.get(Invoice, env["invoice_id"]).status == "paid"
    assert db.get(TaxFiling, env["filing_id"]).status == "client_review"
    db.close()
    history = client.get("/client/tax-summary").text
    assert "UPI (India)" in history and "13,275.00 INR" in history


def test_authorized_payment_is_captured(env, rzp):
    set_rate(env)
    client = as_(env, "client")
    start(client, env["invoice_id"])
    rp = rzp.pay("order_1", status="authorized", method="card")
    assert "saved=" in verify(client, "order_1", rp["id"]).headers["location"]
    assert rzp.payments[rp["id"]]["status"] == "captured"
    db = env["Session"]()
    assert db.query(Payment).one().payment_method == "card_inr"
    db.close()


def test_closed_window_then_paid_late_by_webhook(env, rzp):
    set_rate(env)
    client = as_(env, "client")
    payment_id = start(client, env["invoice_id"]).json()["payment"]
    client.post("/client/payments/razorpay/cancel", data={"payment": payment_id})
    db = env["Session"]()
    assert db.get(Payment, payment_id).status == "failed"
    # a UPI request the client approved on their phone after closing the window
    rp = rzp.pay("order_1")
    assert send(payment_event("payment.captured", rp), "evt_1").json()["result"] == "applied"
    assert send(payment_event("payment.captured", rp), "evt_1").json()["result"] == "duplicate"
    db.expire_all()
    assert db.get(Payment, payment_id).status == "succeeded" and db.get(Invoice, env["invoice_id"]).status == "paid"
    db.close()


def test_failed_attempt_then_success_on_same_order(env, rzp):
    set_rate(env)
    client = as_(env, "client")
    start(client, env["invoice_id"])
    bad = rzp.pay("order_1", status="failed", method="card", error="Your card was declined.")
    send(payment_event("payment.failed", bad), "evt_1")
    db = env["Session"]()
    pay = db.query(Payment).one()
    assert pay.status == "failed" and pay.failure_reason == "Your card was declined."
    good = rzp.pay("order_1", method="netbanking")
    send(payment_event("payment.captured", good), "evt_2")
    db.expire_all()
    pay = db.get(Payment, pay.id)
    assert pay.status == "succeeded" and pay.payment_method == "netbanking" and pay.transaction_reference == good["id"]
    db.close()


def test_webhook_signature_is_checked(env, rzp):
    event = payment_event("payment.captured", {"id": "pay_x", "order_id": "order_x", "status": "captured"})
    assert send(event, "evt_1", secret="wrong").status_code == 400
    assert TestClient(app).post("/webhooks/razorpay", json=event).status_code == 400
    assert send(event, "evt_1").json()["result"] == "ignored"  # signed, but not one of our payments


def test_refunds(env, rzp):
    set_rate(env)
    client, admin = as_(env, "client"), as_(env, "admin")
    start(client, env["invoice_id"])
    rp = rzp.pay("order_1")
    verify(client, "order_1", rp["id"])
    db = env["Session"]()
    pay = db.query(Payment).one()
    page = admin.get(f"/admin/payments/invoices/{env['invoice_id']}").text
    assert "Paid online (Razorpay)" in page and "₹13,275.00 at ₹88.50" in page and "Refund payment" in page
    assert "saved=" in admin.post(f"/admin/payments/payments/{pay.id}/refund", data={"amount": "50"},
                                  follow_redirects=False).headers["location"]
    assert rzp.refunds == [(rp["id"], 442500)]  # $50 at the ₹88.50 the client paid
    db.expire_all()
    assert db.get(Payment, pay.id).refunded_amount == Decimal("50.00")
    assert db.get(Invoice, env["invoice_id"]).status == "partially_paid"
    # Razorpay's webhook for that same refund changes nothing
    send({"event": "refund.processed", "payload": {"payment": {"entity": {**rp, "amount_refunded": 442500}},
                                                   "refund": {"entity": {"id": "rfnd_1"}}}}, "evt_r1")
    db.expire_all()
    assert db.get(Payment, pay.id).refunded_amount == Decimal("50.00")
    # the rest: exactly the rupees still unrefunded
    admin.post(f"/admin/payments/payments/{pay.id}/refund", data={"amount": "100"}, follow_redirects=False)
    assert rzp.refunds[-1] == (rp["id"], 1327500 - 442500)
    db.expire_all()
    assert db.get(Payment, pay.id).status == "refunded" and db.get(Invoice, env["invoice_id"]).status == "issued"
    db.close()


def test_dispute_lost(env, rzp):
    set_rate(env)
    client = as_(env, "client")
    start(client, env["invoice_id"])
    rp = rzp.pay("order_1", method="card")
    verify(client, "order_1", rp["id"])
    send({"event": "payment.dispute.created", "payload": {"payment": {"entity": rp},
                                                          "dispute": {"entity": {"id": "disp_1", "status": "open"}}}}, "evt_d1")
    db = env["Session"]()
    pay = db.query(Payment).one()
    assert pay.dispute_status == "open"
    send({"event": "payment.dispute.lost", "payload": {"payment": {"entity": rp},
                                                       "dispute": {"entity": {"id": "disp_1", "status": "lost"}}}}, "evt_d2")
    db.expire_all()
    assert db.get(Payment, pay.id).status == "refunded"
    db.close()


def test_check_with_razorpay(env, rzp):
    set_rate(env)
    client, admin = as_(env, "client"), as_(env, "admin")
    start(client, env["invoice_id"])
    rzp.pay("order_1", status="failed")
    rzp.pay("order_1")  # paid on a second try; no webhook arrived, the client never came back
    db = env["Session"]()
    pay = db.query(Payment).one()
    assert "Check with Razorpay" in admin.get(f"/admin/payments/invoices/{env['invoice_id']}").text
    assert "saved=" in admin.post(f"/admin/payments/payments/{pay.id}/sync", follow_redirects=False).headers["location"]
    db.expire_all()
    assert db.get(Payment, pay.id).status == "succeeded"
    db.close()


def test_bank_transfer_smart_collect(env, rzp):
    set_rate(env)
    client = as_(env, "client")
    assert "Get bank transfer details" in client.get("/client/make-payment").text
    r = client.post("/client/payments/bank-account", follow_redirects=False)
    assert "bank=1" in r.headers["location"]
    client.post("/client/payments/bank-account", follow_redirects=False)  # asked twice: one account
    assert rzp.accounts == [env["client"]["person"].id]
    page = client.get("/client/make-payment").text
    assert "2223330001234" in page and "RAZR0000001" in page

    rp = {"id": "pay_bt1", "entity": "payment", "amount": 1327500, "currency": "INR", "status": "captured",
          "method": "bank_transfer", "fee": 2360, "order_id": None}
    event = {"event": "virtual_account.credited", "payload": {
        "payment": {"entity": rp}, "virtual_account": {"entity": {"id": "va_1"}},
        "bank_transfer": {"entity": {"utr": "UTIB0000123456"}}}}
    assert send(event, "evt_bt1").json()["result"] == "applied"
    send(event, "evt_bt1")  # redelivered
    send(payment_event("payment.captured", rp), "evt_bt2")  # Razorpay also sends payment.captured for it
    db = env["Session"]()
    pay = db.query(Payment).one()
    assert pay.gateway == "razorpay" and pay.payment_method == "bank_transfer" and pay.status == "succeeded"
    assert pay.amount == Decimal("150.00") and pay.original_amount == Decimal("13275.00")
    assert db.get(Invoice, env["invoice_id"]).status == "paid"
    assert db.query(VirtualAccount).count() == 1
    db.close()


def test_advance_payment_in_inr(env, rzp):
    set_rate(env)
    db = env["Session"]()
    db.get(Invoice, env["invoice_id"]).status = "void"
    db.commit()
    client = as_(env, "client")
    order = start(client, "", pay_amount="100").json()
    assert order["amount"] == 885000
    rp = rzp.pay(order["order_id"])
    verify(client, order["order_id"], rp["id"])
    pay = db.query(Payment).one()
    assert pay.status == "succeeded" and pay.invoice_id is None and pay.amount == Decimal("100.00")
    db.close()
