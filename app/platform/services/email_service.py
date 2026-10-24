"""Outgoing email: password reset links and staff/admin sign-in codes.

EMAIL_BACKEND="console" writes each message to the server log instead of sending it, so local
development and tests never send real mail. EMAIL_BACKEND="smtp" sends through SMTP_HOST.
Messages are rendered from templates/emails/<name>.txt and .html.
"""
from __future__ import annotations

import logging
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

from fastapi import Request

from app.core.config import settings
from app.core.templates import templates

log = logging.getLogger(__name__)


def public_url(request: Request, path: str) -> str:
    """Absolute link for an email: PUBLIC_BASE_URL in production, else the request's own host."""
    return (settings.PUBLIC_BASE_URL or str(request.base_url)).rstrip("/") + path


def _build(to: str, subject: str, template: str, ctx: dict) -> EmailMessage:
    ctx = {"app_name": settings.APP_NAME, **ctx}
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr((settings.EMAIL_FROM_NAME, settings.EMAIL_FROM))
    msg["To"] = to
    if settings.EMAIL_REPLY_TO:
        msg["Reply-To"] = settings.EMAIL_REPLY_TO
    msg["Message-ID"] = make_msgid(domain=settings.EMAIL_FROM.rpartition("@")[2] or None)
    msg.set_content(templates.get_template(f"emails/{template}.txt").render(ctx))
    msg.add_alternative(templates.get_template(f"emails/{template}.html").render(ctx), subtype="html")
    return msg


def send(to: str, subject: str, template: str, **ctx) -> bool:
    """Send one email. Returns False (and logs why) when sending fails; never raises."""
    msg = _build(to, subject, template, ctx)
    if settings.EMAIL_BACKEND != "smtp":
        log.warning("Email not sent (EMAIL_BACKEND=console)\nTo: %s\nSubject: %s\n\n%s",
                    to, subject, msg.get_body(("plain",)).get_content())
        return True
    try:
        context = ssl.create_default_context()
        if settings.SMTP_PORT == 465:
            server = smtplib.SMTP_SSL(settings.SMTP_HOST, settings.SMTP_PORT, context=context, timeout=20)
        else:
            server = smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=20)
            server.starttls(context=context)
        with server:
            if settings.SMTP_USER:
                server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            server.send_message(msg)
    except (smtplib.SMTPException, OSError):
        log.exception("Email to %s (%s) could not be sent", to, subject)
        return False
    return True


def send_password_reset(to: str, link: str, minutes: int) -> bool:
    return send(to, f"Reset your {settings.APP_NAME} password", "password_reset", link=link, minutes=minutes)


def send_login_code(to: str, code: str, purpose: str, minutes: int) -> bool:
    subject = (f"{code} is your {settings.APP_NAME} verification code" if purpose == "signup"
               else f"{code} is your {settings.APP_NAME} sign-in code")
    return send(to, subject, "login_code", code=code, purpose=purpose, minutes=minutes)
