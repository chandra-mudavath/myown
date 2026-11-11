"""Check the payment gateway configuration (Stripe for USD, Razorpay for INR) after keys are added to .env.

    ..\\env\\Scripts\\python.exe check_payments_config.py

Read-only: it makes one harmless read call to each gateway to prove the keys work (no order, charge or refund
is created), checks the webhook secrets and the USD -> INR rate, and prints the webhook URLs to register.
Exit code 0 = everything that is switched on is ready; 1 = something needs fixing.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import app.db_models  # noqa: F401,E402 — registers every model
from app.core.config import settings  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.platform.services import payments, razorpay_api, stripe_api  # noqa: E402

OK, WARN, FAIL = "  [ok]  ", "  [!]   ", "  [x]   "
problems = 0


def say(mark: str, text: str) -> None:
    global problems
    if mark == FAIL:
        problems += 1
    print(mark + text)


def base_url() -> str:
    base = settings.PUBLIC_BASE_URL.rstrip("/")
    if not base:
        say(WARN, "PUBLIC_BASE_URL is blank: return pages use the request's host. Set it to the live https:// address.")
        return "https://<your-site>"
    if not base.startswith("https://"):
        say(WARN, f"PUBLIC_BASE_URL is {base} (fine locally; must be https:// on the live site)")
    return base


def check_stripe(base: str) -> None:
    print("\nStripe  (Pay in USD: card, Apple Pay, Google Pay, ACH)")
    if not stripe_api.enabled():
        say(WARN, "Not set up: STRIPE_SECRET_KEY is blank. Clients see the wire / ACH instructions instead.")
        return
    key = settings.STRIPE_SECRET_KEY
    mode = "TEST" if "_test_" in key else "LIVE" if "_live_" in key else "unknown"
    if key.startswith("pk_"):
        say(FAIL, "STRIPE_SECRET_KEY is a publishable key (pk_). Use the secret (sk_) or restricted (rk_) key.")
        return
    try:
        stripe_api._call("GET", "/balance")
        say(OK, f"Secret key works ({mode} mode)")
    except stripe_api.StripeError as e:
        say(FAIL, f"Secret key rejected: {e}  (a restricted key also needs Balance: read for this check)")
    except Exception as e:  # network etc.
        say(FAIL, f"Could not reach Stripe: {e}")
    if settings.STRIPE_WEBHOOK_SECRET.startswith("whsec_"):
        say(OK, "Webhook secret set")
    else:
        say(FAIL, "STRIPE_WEBHOOK_SECRET missing or not a whsec_ value: payments would only update when the client returns")
    if len(settings.STRIPE_STATEMENT_DESCRIPTOR) > 22:
        say(FAIL, "STRIPE_STATEMENT_DESCRIPTOR is longer than 22 characters")
    print(f"        Webhook URL: {base}/webhooks/stripe")
    print("        Events: checkout.session.completed, checkout.session.async_payment_succeeded,")
    print("                checkout.session.async_payment_failed, checkout.session.expired, charge.refunded,")
    print("                charge.dispute.created, charge.dispute.closed")


def check_razorpay(base: str, db) -> None:
    print("\nRazorpay  (Pay in INR: UPI / QR, cards, net banking, wallets)")
    if not razorpay_api.enabled():
        say(WARN, "Not set up: RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET blank. Clients see the manual UPI QR / bank transfer form.")
        return
    key = settings.RAZORPAY_KEY_ID
    mode = "TEST" if key.startswith("rzp_test_") else "LIVE" if key.startswith("rzp_live_") else "unknown"
    try:
        razorpay_api._call("GET", "/payments?count=1")
        say(OK, f"Keys work ({mode} mode)")
    except razorpay_api.RazorpayError as e:
        say(FAIL, f"Keys rejected: {e}")
    except Exception as e:
        say(FAIL, f"Could not reach Razorpay: {e}")
    if settings.RAZORPAY_WEBHOOK_SECRET:
        say(OK, "Webhook secret set")
    else:
        say(FAIL, "RAZORPAY_WEBHOOK_SECRET is blank: payments would only update when the client finishes in the browser")
    rate = payments.inr_rate(db)
    if rate:
        say(OK, f"USD -> INR rate set: $1 = Rs {rate}")
    else:
        say(FAIL, "No USD -> INR rate yet: set it in Admin > Settings > Payments (Razorpay stays off until then)")
    if settings.RAZORPAY_SMART_COLLECT:
        say(OK, "Smart Collect on (bank transfer to a per-client account) - make sure Razorpay has activated it")
    else:
        say(WARN, "Smart Collect off: bank transfer uses PAYMENT_BANK_DETAILS_IN (admin verifies)")
    print(f"        Webhook URL: {base}/webhooks/razorpay")
    print("        Events: payment.captured, payment.failed, order.paid, refund.processed,")
    print("                payment.dispute.created, payment.dispute.won, payment.dispute.lost, virtual_account.credited")


def check_fallbacks() -> None:
    print("\nWithout a gateway (always available as a backup)")
    for name, value, what in (("PAYMENT_UPI_ID", settings.PAYMENT_UPI_ID, "UPI QR (INR)"),
                              ("PAYMENT_BANK_DETAILS_IN", settings.PAYMENT_BANK_DETAILS_IN.strip(), "India bank transfer (INR)"),
                              ("PAYMENT_BANK_DETAILS", settings.PAYMENT_BANK_DETAILS.strip(), "US wire / ACH (USD)")):
        say(OK if value else WARN, f"{what}: {'set' if value else name + ' is blank'}")


def main() -> int:
    print(f"Payment configuration check ({settings.APP_NAME})")
    base = base_url()
    db = SessionLocal()
    try:
        check_stripe(base)
        check_razorpay(base, db)
        check_fallbacks()
    finally:
        db.close()
    print("\n" + ("All good." if not problems else f"{problems} thing(s) to fix above."))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
