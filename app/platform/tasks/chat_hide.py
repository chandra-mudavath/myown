"""Daily job: hide chat threads whose 3-month period has ended. Hidden threads stay in the
database for audit; nothing is deleted.

Run once a day from any scheduler (cron, Windows Task Scheduler, or a Celery beat task):

    python -m app.platform.tasks.chat_hide
"""
from __future__ import annotations

from app.core.database import SessionLocal
import app.db_models  # noqa: F401  (registers models)
from app.platform.services.chat_service import hide_due_threads


def run() -> int:
    db = SessionLocal()
    try:
        return hide_due_threads(db)
    finally:
        db.close()


if __name__ == "__main__":
    print(f"Hidden {run()} chat thread(s).")
