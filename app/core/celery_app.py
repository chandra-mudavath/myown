"""Celery: background jobs (emails now; reminders, reports, cleanups later) through Redis.

CELERY_TASK_ALWAYS_EAGER=true (the default) runs every task inside the web process, so local
development and tests need no Redis or worker. Production sets it to false and runs:

    celery -A app.core.celery_app worker -l info      (Windows dev: add --pool=solo)
    celery -A app.core.celery_app beat -l info        (scheduled jobs, run exactly one)
"""
from __future__ import annotations

from celery import Celery
from celery.schedules import crontab

from app.core.config import settings

celery_app = Celery(
    "covrenttax",
    broker=settings.CELERY_BROKER_URL,
    include=["app.platform.tasks.email_tasks", "app.platform.tasks.chat_hide"],
)

celery_app.conf.update(
    task_always_eager=settings.CELERY_TASK_ALWAYS_EAGER,
    task_ignore_result=True,            # no result backend; tasks report through logs
    task_acks_late=True,                # a task lost to a worker crash is redelivered
    worker_prefetch_multiplier=1,
    task_serializer="json",
    accept_content=["json"],
    timezone="Asia/Kolkata",
    enable_utc=True,
    broker_connection_retry_on_startup=True,
    beat_schedule={
        "hide-closed-chat-threads": {
            "task": "chat.hide_due_threads",
            "schedule": crontab(hour=2, minute=30),
        },
    },
)
