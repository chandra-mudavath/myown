"""Celery tasks that send app emails, retried when the mail provider is unreachable.

Request handlers call enqueue(...) from a FastAPI background task, so the page never waits on
Redis or SMTP.
"""
from __future__ import annotations

import logging

from kombu.exceptions import OperationalError

from app.core.celery_app import celery_app
from app.platform.services import email_service

log = logging.getLogger(__name__)

_RETRY = {"bind": True, "max_retries": 5, "default_retry_delay": 60}


def enqueue(task, *args) -> None:
    """Queue a task; if Redis is down, send it in this process instead of losing the email."""
    try:
        task.delay(*args)
    except OperationalError:
        log.exception("Task queue unreachable; running %s inline", task.name)
        task.apply(args=args)


def _retry_on_failure(task, sent: bool) -> None:
    # In eager mode a retry would loop inside the request; email_service has already logged it.
    if not sent and not task.request.is_eager:
        raise task.retry()


@celery_app.task(name="email.password_reset", **_RETRY)
def send_password_reset(self, to: str, link: str, minutes: int) -> None:
    _retry_on_failure(self, email_service.send_password_reset(to, link, minutes))


@celery_app.task(name="email.login_code", **_RETRY)
def send_login_code(self, to: str, code: str, purpose: str, minutes: int) -> None:
    _retry_on_failure(self, email_service.send_login_code(to, code, purpose, minutes))
