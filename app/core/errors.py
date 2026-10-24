"""Error screens: every error a person can see in the browser, by code.

Each screen has a title and two short lines: what went wrong, then what to do. Codes never appear on
screen; they go into JSON responses ("code") and the server log so support can find the exact case.

Raise AppError(code) to show a screen (or send {"detail", "code"} to a fetch() call). Plain
HTTPExceptions still work: error_handlers.py picks a screen from the status code.
"""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException


@dataclass(frozen=True)
class Screen:
    code: str
    status: int
    icon: str       # name in errors/_icons.html
    tone: str       # brand | coral | amber
    title: str
    problem: str    # what went wrong (may use {params})
    fix: str        # what to do
    actions: tuple[str, ...]  # first one is the primary button; see error_handlers._action


SCREENS: dict[str, Screen] = {s.code: s for s in (
    # Sign-in and access
    Screen("AUTH-001", 401, "lock", "brand", "Please sign in",
           "You aren't signed in.", "Sign in and we'll bring you back to this page.", ("sign_in", "home")),
    Screen("AUTH-002", 401, "clock", "brand", "Your session has expired",
           "You were signed out after a long time away.", "Sign in again to continue.", ("sign_in_again", "home")),
    Screen("AUTH-003", 401, "userx", "brand", "Your account isn't active",
           "This account has been deactivated.", "Contact support to reactivate it.", ("support", "home")),
    Screen("AUTH-004", 403, "shield", "brand", "You don't have access",
           "This page is for clients. You're signed in as {you}.",
           "Go to your dashboard, or sign in with a different account.", ("dashboard", "switch_account")),
    Screen("AUTH-005", 403, "shield", "brand", "You don't have access",
           "This page is for the tax team. You're signed in as {you}.",
           "Go to your dashboard, or sign in with a different account.", ("dashboard", "switch_account")),
    Screen("AUTH-006", 403, "shield", "brand", "You don't have access",
           "This page is for administrators. You're signed in as {you}.",
           "Go to your dashboard, or sign in with a different account.", ("dashboard", "switch_account")),
    Screen("AUTH-007", 403, "shield", "brand", "You don't have access",
           "Your account can't open this page.",
           "Go to your dashboard, or sign in with a different account.", ("dashboard", "switch_account")),
    Screen("AUTH-008", 403, "shield", "brand", "You don't have access",
           "Client cases, clients and documents aren't available to HR accounts.",
           "Use Messages or your profile, or sign in with a different account.", ("dashboard", "switch_account")),
    Screen("AUTH-009", 403, "lock", "brand", "Set your own password",
           "You signed in with a temporary password from an administrator.",
           "Choose your own password to continue.", ("change_password", "switch_account")),
    Screen("AUTH-015", 404, "link", "brand", "This sign-up link isn't valid",
           "This sign-up link is no longer valid.", "Ask an administrator for the current link.", ("sign_in", "home")),
    # Pages and records
    Screen("SYS-001", 404, "compass", "brand", "We can't find that page",
           "This page doesn't exist.", "Check the link, or go to your dashboard.", ("dashboard", "back")),
    Screen("SYS-004", 404, "filesearch", "brand", "{thing} not found",
           "This {thing_lower} doesn't exist or was deleted.", "Go back to the list and open it from there.",
           ("list", "dashboard")),
    Screen("DOC-009", 404, "filex", "brand", "This file isn't available",
           "{name} couldn't be found on our servers.", "Contact your tax team so they can upload it again.",
           ("back", "dashboard")),
    # Requests
    Screen("SYS-006", 405, "refresh", "brand", "This link can't be opened directly",
           "This action can only run from its button.", "Go back and press the button again.", ("back", "dashboard")),
    Screen("SYS-011", 400, "form", "brand", "We couldn't process that request",
           "Something in the form didn't look right, so nothing was saved.", "Go back, refresh the page and try again.",
           ("back", "dashboard")),
    Screen("SYS-012", 403, "clock", "brand", "This form has expired",
           "For your security this form can't be sent anymore. Nothing was saved.",
           "Refresh the page and submit the form again.", ("back", "dashboard")),
    Screen("SYS-008", 413, "upload", "brand", "This file is too large",
           "Files can be up to {max_mb} MB.", "Go back and choose a smaller file, or compress it first.", ("back",)),
    # Server
    Screen("SYS-002", 500, "alert", "coral", "Something went wrong on our side",
           "We couldn't finish your request. Nothing was saved.",
           "Try again in a moment. If it keeps happening, contact support.", ("reload", "home")),
    Screen("SYS-007", 503, "wrench", "amber", "We'll be right back",
           "{app_name} is being updated.", "Please check back in a few minutes. Your data is safe.", ("reload",)),
    Screen("SYS-009", 502, "server", "coral", "{app_name} isn't responding",
           "Our server didn't answer.", "Wait a minute, then try again.", ("reload",)),
    Screen("SYS-010", 504, "hourglass", "amber", "This is taking too long",
           "The server took too long to answer. Your request may still finish.",
           "Wait a minute and check before trying again.", ("reload", "dashboard")),
)}

# The screen for a plain HTTPException (no code) with this status.
BY_STATUS = {400: "SYS-011", 401: "AUTH-001", 403: "AUTH-007", 404: "SYS-001", 405: "SYS-006",
             413: "SYS-008", 422: "SYS-011", 500: "SYS-002", 502: "SYS-009", 503: "SYS-007", 504: "SYS-010"}


class AppError(HTTPException):
    """An HTTPException that names its error screen. detail stays the message JSON callers get."""

    def __init__(self, code: str, detail: str | None = None, *, headers: dict | None = None, **params):
        screen = SCREENS[code]
        super().__init__(screen.status, detail or screen.problem.format_map(_Blank(params)), headers=headers)
        self.code = code
        self.params = params


class _Blank(dict):
    """format_map helper: a missing {param} renders as an empty string instead of raising."""

    def __missing__(self, key):
        return ""
