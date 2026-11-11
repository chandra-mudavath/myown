"""Thin Razorpay REST client: orders for Checkout, payments, refunds, Smart Collect accounts and signatures.

Pay in INR goes to the CovrentTax India account through Razorpay. The client pays in Razorpay's Checkout window
(UPI / QR, debit and credit cards, net banking, wallets), so card and bank details never reach us. Which methods
the window shows, international cards and auto-capture are set in the Razorpay Dashboard, not here.
Amounts are in paise (₹1 = 100 paise).
"""
from __future__ import annotations

import hashlib
import hmac
import json
from decimal import Decimal

import httpx

from app.core.config import settings

API = "https://api.razorpay.com/v1"


class RazorpayError(Exception):
    """A Razorpay API call failed. ``code`` is Razorpay's error code when it sent one (e.g. BAD_REQUEST_ERROR)."""

    def __init__(self, message: str, code: str = "", status: int = 0):
        super().__init__(message)
        self.code, self.status = code, status


def enabled() -> bool:
    return bool(settings.RAZORPAY_KEY_ID and settings.RAZORPAY_KEY_SECRET)


def smart_collect_enabled() -> bool:
    """Bank transfer to a client's own account number. Razorpay has to switch Smart Collect on for the account first."""
    return enabled() and settings.RAZORPAY_SMART_COLLECT


def paise(amount: Decimal) -> int:
    return int((amount * 100).quantize(Decimal("1")))


def _call(method: str, path: str, body: dict | None = None) -> dict:
    r = httpx.request(method, f"{API}{path}", json=body, auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET),
                      timeout=30)
    if r.status_code >= 400:
        try:
            err = r.json().get("error") or {}
        except ValueError:
            err = {}
        raise RazorpayError(f"Razorpay {method} {path} failed ({r.status_code} {err.get('code', '')}: "
                            f"{err.get('description', '')}).", err.get("code", ""), r.status_code)
    return r.json() if r.content else {}


def create_order(*, amount_inr: Decimal, receipt: str, notes: dict) -> dict:
    """An INR order for Checkout to pay. Razorpay won't take a second payment on an order that is already paid."""
    return _call("POST", "/orders", {"amount": paise(amount_inr), "currency": "INR", "receipt": receipt[:40],
                                     "notes": {k: str(v)[:256] for k, v in notes.items()}})


def get_payment(payment_id: str) -> dict:
    return _call("GET", f"/payments/{payment_id}")


def order_payments(order_id: str) -> list[dict]:
    return _call("GET", f"/orders/{order_id}/payments").get("items", [])


def capture_payment(payment_id: str, amount_paise: int) -> dict:
    """Capture an authorized payment (only needed if auto-capture is off in the Dashboard)."""
    return _call("POST", f"/payments/{payment_id}/capture", {"amount": amount_paise, "currency": "INR"})


def refund_payment(payment_id: str, amount_paise: int, notes: dict) -> dict:
    return _call("POST", f"/payments/{payment_id}/refund", {"amount": amount_paise, "speed": "normal",
                                                            "notes": {k: str(v)[:256] for k, v in notes.items()}})


def create_virtual_account(*, client_id: str, client_name: str, description: str) -> dict:
    """A bank account number (Smart Collect) only this client pays into, so each transfer is matched to them."""
    return _call("POST", "/virtual_accounts", {
        "receivers": {"types": ["bank_account"]},
        "description": description[:2048],
        "notes": {"client_id": client_id, "client_name": client_name[:256]},
    })


def bank_receiver(account: dict) -> dict:
    """The bank account part of a virtual account ({} when there is none)."""
    for receiver in account.get("receivers") or []:
        if receiver.get("entity") == "bank_account":
            return receiver
    return {}


def _sign(secret: str, message: bytes) -> str:
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def checkout_signature_ok(order_id: str, payment_id: str, signature: str) -> bool:
    """The signature Checkout hands the browser after a payment: HMAC-SHA256 of "order_id|payment_id" with the key secret."""
    if not (order_id and payment_id and signature and settings.RAZORPAY_KEY_SECRET):
        return False
    return hmac.compare_digest(_sign(settings.RAZORPAY_KEY_SECRET, f"{order_id}|{payment_id}".encode()), signature)


def verify_event(payload: bytes, signature: str) -> dict:
    """Check X-Razorpay-Signature (HMAC-SHA256 of the raw body with the webhook secret) and return the event."""
    secret = settings.RAZORPAY_WEBHOOK_SECRET
    if not secret:
        raise RazorpayError("RAZORPAY_WEBHOOK_SECRET isn't set.")
    if not signature or not hmac.compare_digest(_sign(secret, payload), signature):
        raise RazorpayError("Webhook signature doesn't match.")
    try:
        return json.loads(payload)
    except ValueError as e:
        raise RazorpayError("Webhook body isn't JSON.") from e
