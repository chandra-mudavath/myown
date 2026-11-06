"""Payment gateway webhooks: the gateway's servers call these, never a browser, so there is no sign-in.

Each request is trusted only after its signature checks out against the webhook secret. Answers:
200 handled / ignored / a repeat; 400 a bad signature (Stripe doesn't retry those); 500 we couldn't apply it yet
(e.g. Stripe unreachable when reading the session back), so Stripe retries for up to 3 days.
"""
import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.platform.services import payments, stripe_api

router = APIRouter(prefix="/webhooks", tags=["webhooks"])
log = logging.getLogger(__name__)


@router.post("/stripe", include_in_schema=False)
async def stripe_webhook(request: Request, db: Session = Depends(get_db)):
    payload = await request.body()  # the raw bytes: the signature is over exactly what Stripe sent
    try:
        event = stripe_api.verify_event(payload, request.headers.get("stripe-signature", ""))
    except stripe_api.StripeError as e:
        log.warning("Stripe webhook refused: %s", e)
        return JSONResponse({"received": False}, status_code=400)
    try:
        result = payments.handle_stripe_event(db, event)
    except Exception:
        db.rollback()
        log.exception("Stripe webhook %s (%s) failed; Stripe will retry", event.get("id"), event.get("type"))
        return JSONResponse({"received": False}, status_code=500)
    return {"received": True, "result": result}
