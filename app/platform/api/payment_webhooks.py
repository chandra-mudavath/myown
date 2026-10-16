"""Payment gateway webhooks. No login: each event's signature is verified with the gateway instead."""
import json

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.platform.services import payments, paypal

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/paypal")
async def paypal_webhook(request: Request, db: Session = Depends(get_db)):
    try:
        event = json.loads(await request.body())
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid JSON.")
    if not paypal.enabled() or not paypal.verify_webhook(dict(request.headers), event):
        raise HTTPException(status_code=400, detail="Webhook could not be verified.")
    payments.apply_paypal_event(db, event)
    return {"ok": True}
