"""Thin Stripe REST client: hosted Checkout, refunds and webhook signatures.

Only what Pay in USD needs. Card and bank details never reach us; the client pays on Stripe's own Checkout page.
Which methods Checkout shows (cards, Apple Pay, Google Pay, ACH bank debit) is set in the Stripe Dashboard
(Settings > Payment methods), so turning one on or off needs no code change.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from decimal import Decimal

import httpx

from app.core.config import settings

API = "https://api.stripe.com/v1"
API_VERSION = "2024-06-20"     # pinned so a Dashboard upgrade can't change the shapes read here
SIGNATURE_TOLERANCE = 300      # seconds; an older signed webhook is refused (replay protection)
CHECKOUT_MINUTES = 60          # an unpaid Checkout page expires after this (Stripe allows 30 min to 24 h)


class StripeError(Exception):
    """A Stripe API call failed. ``code`` is Stripe's error code when it sent one (e.g. charge_already_refunded)."""

    def __init__(self, message: str, code: str = "", status: int = 0):
        super().__init__(message)
        self.code, self.status = code, status


def enabled() -> bool:
    return bool(settings.STRIPE_SECRET_KEY)


def cents(amount: Decimal) -> int:
    return int((amount * 100).quantize(Decimal("1")))


def _form(data: dict, prefix: str = "") -> list[tuple[str, str]]:
    """Stripe takes nested form fields: {"a": {"b": 1}, "c": [2]} -> a[b]=1, c[0]=2."""
    out: list[tuple[str, str]] = []
    for key, value in data.items():
        name = f"{prefix}[{key}]" if prefix else str(key)
        if isinstance(value, dict):
            out += _form(value, name)
        elif isinstance(value, (list, tuple)):
            for i, item in enumerate(value):
                out += _form(item, f"{name}[{i}]") if isinstance(item, dict) else [(f"{name}[{i}]", str(item))]
        elif value is not None:
            out.append((name, str(value).lower() if isinstance(value, bool) else str(value)))
    return out


def _call(method: str, path: str, data: dict | None = None, idempotency_key: str | None = None) -> dict:
    headers = {"Stripe-Version": API_VERSION}
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key  # a retried request with the same key isn't done twice
    r = httpx.request(method, f"{API}{path}", auth=(settings.STRIPE_SECRET_KEY, ""), headers=headers, timeout=30,
                      params=_form(data) if data and method == "GET" else None,
                      data=_form(data) if data and method != "GET" else None)
    if r.status_code >= 400:
        try:
            err = r.json().get("error") or {}
        except ValueError:
            err = {}
        raise StripeError(f"Stripe {method} {path} failed ({r.status_code} {err.get('code', '')}).",
                          err.get("code", ""), r.status_code)
    return r.json()


def create_checkout_session(*, amount: Decimal, description: str, payment_id: str, payment_number: str, reference: str,
                            customer_email: str, success_url: str, cancel_url: str) -> tuple[str, str]:
    """A one-off USD Checkout page for `amount`. Returns (session id, the URL to send the client to)."""
    metadata = {"payment_id": payment_id, "payment_number": payment_number, "reference": reference}
    intent = {"description": description[:1000], "metadata": metadata}
    if settings.STRIPE_STATEMENT_DESCRIPTOR:
        intent["statement_descriptor_suffix"] = settings.STRIPE_STATEMENT_DESCRIPTOR[:22]
    body = {
        "mode": "payment",
        "line_items": [{"quantity": 1, "price_data": {
            "currency": "usd", "unit_amount": cents(amount), "product_data": {"name": description[:250]}}}],
        "client_reference_id": payment_id,
        "metadata": metadata,
        "payment_intent_data": intent,
        "success_url": success_url,
        "cancel_url": cancel_url,
        "expires_at": int(time.time()) + CHECKOUT_MINUTES * 60,
        "customer_email": customer_email or None,
    }
    session = _call("POST", "/checkout/sessions", body, f"checkout-{payment_number}")
    if not session.get("url"):
        raise StripeError("Stripe didn't return a Checkout URL.")
    return session["id"], session["url"]


def get_checkout_session(session_id: str) -> dict:
    """The session with its PaymentIntent, the charge and the fee (balance transaction) filled in."""
    return _call("GET", f"/checkout/sessions/{session_id}",
                 {"expand": ["payment_intent.latest_charge.balance_transaction"]})


def expire_checkout_session(session_id: str) -> dict:
    """Close an unpaid Checkout page so it can't be paid any more. Fails if the client already paid."""
    return _call("POST", f"/checkout/sessions/{session_id}/expire")


def create_refund(payment_intent_id: str, amount: Decimal, idempotency_key: str, reason: str = "requested_by_customer") -> dict:
    return _call("POST", "/refunds", {"payment_intent": payment_intent_id, "amount": cents(amount), "reason": reason},
                 idempotency_key)


def verify_event(payload: bytes, signature_header: str, now: float | None = None) -> dict:
    """Check the Stripe-Signature header (HMAC-SHA256 of "timestamp.body" with the webhook secret) and return the event.

    Raises StripeError when the signature is missing, wrong or too old."""
    secret = settings.STRIPE_WEBHOOK_SECRET
    if not secret:
        raise StripeError("STRIPE_WEBHOOK_SECRET isn't set.")
    parts = [p.split("=", 1) for p in (signature_header or "").split(",") if "=" in p]
    timestamps = [v for k, v in parts if k == "t"]
    signatures = [v for k, v in parts if k == "v1"]
    if not timestamps or not signatures or not timestamps[0].isdigit():
        raise StripeError("Malformed Stripe-Signature header.")
    expected = hmac.new(secret.encode(), timestamps[0].encode() + b"." + payload, hashlib.sha256).hexdigest()
    if not any(hmac.compare_digest(expected, s) for s in signatures):
        raise StripeError("Webhook signature doesn't match.")
    if abs((now or time.time()) - int(timestamps[0])) > SIGNATURE_TOLERANCE:
        raise StripeError("Webhook signature is too old.")
    try:
        return json.loads(payload)
    except ValueError as e:
        raise StripeError("Webhook body isn't JSON.") from e
