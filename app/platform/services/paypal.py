"""Thin PayPal REST client (Orders v2 + webhook verification).

Only what checkout needs: create an order, capture it, read it, and verify a webhook's signature.
Card data never reaches us; the client approves the order on PayPal's own pages.
"""
from __future__ import annotations

import time
from decimal import Decimal

import httpx

from app.core.config import settings

_token: dict = {"value": "", "expires": 0.0}


class PayPalError(Exception):
    """A PayPal API call failed. ``issue`` is PayPal's error code when it sent one (e.g. ORDER_ALREADY_CAPTURED)."""

    def __init__(self, message: str, issue: str = ""):
        super().__init__(message)
        self.issue = issue


def enabled() -> bool:
    return bool(settings.PAYPAL_CLIENT_ID and settings.PAYPAL_CLIENT_SECRET)


def _base() -> str:
    return "https://api-m.paypal.com" if settings.PAYPAL_MODE == "live" else "https://api-m.sandbox.paypal.com"


def _access_token() -> str:
    if _token["value"] and _token["expires"] > time.time() + 60:
        return _token["value"]
    r = httpx.post(f"{_base()}/v1/oauth2/token", data={"grant_type": "client_credentials"},
                   auth=(settings.PAYPAL_CLIENT_ID, settings.PAYPAL_CLIENT_SECRET), timeout=20)
    if r.status_code != 200:
        raise PayPalError(f"PayPal sign-in failed ({r.status_code}).")
    body = r.json()
    _token.update(value=body["access_token"], expires=time.time() + int(body.get("expires_in", 300)))
    return _token["value"]


def _call(method: str, path: str, json: dict | None = None, request_id: str | None = None) -> dict:
    headers = {"Authorization": f"Bearer {_access_token()}", "Content-Type": "application/json"}
    if request_id:
        headers["PayPal-Request-Id"] = request_id  # PayPal treats a repeat with the same id as the same request
    r = httpx.request(method, f"{_base()}{path}", json=json, headers=headers, timeout=30)
    if r.status_code >= 400:
        try:
            details = r.json().get("details") or [{}]
            issue = details[0].get("issue", "")
        except ValueError:
            issue = ""
        raise PayPalError(f"PayPal {method} {path} failed ({r.status_code} {issue}).", issue)
    return r.json() if r.content else {}


def create_order(*, amount: Decimal, currency: str, reference: str, payment_number: str, custom_id: str, description: str,
                 return_url: str, cancel_url: str) -> tuple[str, str]:
    """Create an order to capture later. Returns (order id, the URL to send the client to)."""
    body = {
        "intent": "CAPTURE",
        "purchase_units": [{
            "reference_id": reference,       # our invoice number
            "custom_id": custom_id,          # our payment id; comes back on captures and webhooks
            "invoice_id": payment_number,    # shown to the payer; PayPal won't take two payments with the same one
            "description": description[:127],
            "amount": {"currency_code": currency, "value": f"{amount:.2f}"},
        }],
        "payment_source": {"paypal": {"experience_context": {
            "brand_name": settings.APP_NAME[:127],
            "shipping_preference": "NO_SHIPPING",
            "user_action": "PAY_NOW",
            "return_url": return_url,
            "cancel_url": cancel_url,
        }}},
    }
    order = _call("POST", "/v2/checkout/orders", body, f"create-{payment_number}")
    link = next((l["href"] for l in order.get("links", []) if l.get("rel") in ("payer-action", "approve")), "")
    if not link:
        raise PayPalError("PayPal didn't return an approval link.")
    return order["id"], link


def capture_order(order_id: str, request_id: str) -> dict:
    """Capture an approved order and return the order. Capturing twice returns the existing capture."""
    try:
        return _call("POST", f"/v2/checkout/orders/{order_id}/capture", {}, request_id)
    except PayPalError as e:
        if e.issue == "ORDER_ALREADY_CAPTURED":
            return get_order(order_id)
        raise


def get_order(order_id: str) -> dict:
    return _call("GET", f"/v2/checkout/orders/{order_id}")


def first_capture(order: dict) -> dict:
    """The capture inside an order ({} when there is none)."""
    for unit in order.get("purchase_units", []):
        for cap in (unit.get("payments") or {}).get("captures", []):
            return cap
    return {}


def verify_webhook(headers: dict, event: dict) -> bool:
    """Ask PayPal whether a webhook really came from them."""
    if not settings.PAYPAL_WEBHOOK_ID:
        return False
    h = {k.lower(): v for k, v in headers.items()}
    body = {
        "auth_algo": h.get("paypal-auth-algo", ""),
        "cert_url": h.get("paypal-cert-url", ""),
        "transmission_id": h.get("paypal-transmission-id", ""),
        "transmission_sig": h.get("paypal-transmission-sig", ""),
        "transmission_time": h.get("paypal-transmission-time", ""),
        "webhook_id": settings.PAYPAL_WEBHOOK_ID,
        "webhook_event": event,
    }
    try:
        return _call("POST", "/v1/notifications/verify-webhook-signature", body).get("verification_status") == "SUCCESS"
    except PayPalError:
        return False
