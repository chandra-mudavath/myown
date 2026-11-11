"""Scheduled HR emails (Celery beat, see app/core/celery_app.py). Each one is sent only when HR has
turned it on in HR Settings. Can also be run by hand:

    python -m app.platform.tasks.hr_tasks checkins | summary | expiring | offers
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta

from app.core.celery_app import celery_app
from app.core.database import SessionLocal
import app.db_models  # noqa: F401  (registers models)
from app.modules.hr.models.records import HrDocument
from app.modules.hr.services import documents, hr_settings, notify, people, staff_files, time_off
from app.platform.models.auth import AuthAccount


def _active_staff(db):
    return people.staff_query(db).filter(AuthAccount.is_active.is_(True)).all()


@celery_app.task(name="hr.missed_check_ins")
def missed_check_ins() -> int:
    """Working days, after the check-in time: remind whoever hasn't checked in and isn't on leave."""
    db = SessionLocal()
    try:
        cutoff = hr_settings.get(db, "check_in_cutoff") or "10:00"
        h, m = (int(p) for p in cutoff.split(":"))
        now = datetime.now(time_off.LOCAL_TZ)
        if (now.hour, now.minute) < (h, m):
            return 0
        missing = time_off.not_checked_in_today(db, _active_staff(db))
        for staff in missing:
            notify.missed_check_in(db, notify.base_url(), staff, now.date())
        return len(missing)
    finally:
        db.close()


@celery_app.task(name="hr.daily_summary")
def daily_summary() -> int:
    db = SessionLocal()
    try:
        staff = _active_staff(db)
        day = time_off.today()
        if day.weekday() in hr_settings.week_off_days(db):
            return 0
        codes = time_off.month_grid(db, staff, day.year, day.month)
        present = sum(1 for s in staff if codes[s.id][day.day - 1] in ("P", "HALF"))
        missing = [people.full_name(s) for s in time_off.not_checked_in_today(db, staff)]
        notify.daily_summary(db, notify.base_url(), day, present, time_off.on_leave_today(db, staff), missing)
        return present
    finally:
        db.close()


@celery_app.task(name="hr.documents_expiring")
def documents_expiring() -> int:
    db = SessionLocal()
    try:
        rows = [(people.full_name(d.staff), d.document_type.name, d.expiry_date) for d in staff_files.expiring(db)]
        if rows:
            notify.documents_expiring(db, notify.base_url(), rows)
        return len(rows)
    finally:
        db.close()


@celery_app.task(name="hr.offer_emails")
def offer_emails() -> int:
    """Offer reminder 2 days before the accept-by date; welcome email 3 days before joining."""
    db = SessionLocal()
    try:
        today, sent = time_off.today(), 0
        for doc in db.query(HrDocument).filter(HrDocument.kind == "OFFER", HrDocument.status == "final",
                                               HrDocument.offer_status.in_(("sent", "accepted"))):
            d = doc.data
            if doc.offer_status == "sent" and d.get("accept_by") == (today + timedelta(days=2)).isoformat():
                sent += notify.offer_reminder(db, doc, documents.render(db, doc))
            if doc.offer_status == "accepted" and d.get("joining_date") == (today + timedelta(days=3)).isoformat():
                sent += notify.welcome(db, doc)
        return sent
    finally:
        db.close()


if __name__ == "__main__":
    jobs = {"checkins": missed_check_ins, "summary": daily_summary, "expiring": documents_expiring, "offers": offer_emails}
    job = jobs.get(sys.argv[1] if len(sys.argv) > 1 else "")
    print(job() if job else f"Choose one of: {', '.join(jobs)}")
