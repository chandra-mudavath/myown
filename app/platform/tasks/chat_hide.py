"""Daily job: hide chat threads whose 3-month period has ended. Hidden threads stay in the
database for audit; nothing is deleted.

Celery beat runs it daily (see app/core/celery_app.py). It can also be run by hand:

    python -m app.platform.tasks.chat_hide
"""
from __future__ import annotations

from app.core.celery_app import celery_app
from app.core.database import SessionLocal
import app.db_models  # noqa: F401  (registers models)
from app.platform.services.chat_service import hide_due_threads


@celery_app.task(name="chat.hide_due_threads")
def run() -> int:
    db = SessionLocal()
    try:
        return hide_due_threads(db)
    finally:
        db.close()


if __name__ == "__main__":
    print(f"Hidden {run()} chat thread(s).")
