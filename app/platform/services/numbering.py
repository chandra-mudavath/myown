"""Human-readable numbers for every record the UI shows.

UUIDs stay the primary keys and are what URLs and foreign keys use. Each record people see
also gets a readable number (CLI-00000001, FLI_0000001, DOC-0000001, ...). Numbers come from
the id_sequences table, one counter row per kind, incremented with a single UPDATE, so
numbers never repeat even after deletes or concurrent sign-ups.

A before_insert hook fills the number in automatically, so code that creates a record
doesn't set it. Setting one explicitly (e.g. a seed script) is still respected.
"""
from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import event, func, insert, select, update
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from app.platform.models.auth import AuthAccount
from app.platform.models.billing import Invoice, Payment
from app.platform.models.case_workflow import Acknowledgment, IrsSubmission
from app.platform.models.chat import ChatThread
from app.modules.client.models.client import Client
from app.modules.admin.models.admin import Admin
from app.platform.models.filing_document import FilingDocument
from app.modules.hr.models.hr import HR
from app.platform.models.id_sequence import IdSequence
from app.modules.staff.models.staff import Staff
from app.modules.staff.models.staff_records import StaffDocument, StaffEmploymentDetails
from app.platform.models.tax_filing import TaxFiling


@dataclass(frozen=True)
class SequenceSpec:
    prefix: str
    width: int
    model: type
    column: str


# Formats match the numbers already in use, so existing numbers stay valid.
SEQUENCES: dict[str, SequenceSpec] = {
    "ACCOUNT_CLIENT": SequenceSpec("CLT-", 8, AuthAccount, "account_number"),
    "ACCOUNT_STAFF": SequenceSpec("STF-", 8, AuthAccount, "account_number"),
    "ACCOUNT_ADMIN": SequenceSpec("ADM-", 8, AuthAccount, "account_number"),
    "ACCOUNT_HR": SequenceSpec("HR-", 8, AuthAccount, "account_number"),
    "CLIENT": SequenceSpec("CLI-", 8, Client, "client_number"),
    "STAFF": SequenceSpec("STF-", 8, Staff, "staff_number"),
    "ADMIN": SequenceSpec("ADM-", 8, Admin, "admin_number"),
    "HR": SequenceSpec("HR-", 8, HR, "hr_number"),
    "EMPLOYEE": SequenceSpec("EMP-", 6, StaffEmploymentDetails, "employee_number"),
    "CASE": SequenceSpec("FLI_", 7, TaxFiling, "case_number"),
    "DOCUMENT": SequenceSpec("DOC-", 7, FilingDocument, "document_number"),
    "STAFF_DOCUMENT": SequenceSpec("SDOC-", 7, StaffDocument, "document_number"),
    "IRS_SUBMISSION": SequenceSpec("SUB-", 7, IrsSubmission, "submission_number"),
    "ACKNOWLEDGMENT": SequenceSpec("ACK-", 7, Acknowledgment, "ack_number"),
    "INVOICE": SequenceSpec("INV-", 7, Invoice, "invoice_number"),
    "PAYMENT": SequenceSpec("PAY-", 7, Payment, "payment_number"),
    "CHAT_THREAD": SequenceSpec("MSG-", 7, ChatThread, "thread_number"),
}


def _account_sequence(account: AuthAccount) -> str:
    account_type = getattr(account.account_type, "value", account.account_type)
    return f"ACCOUNT_{account_type}"


# Which sequence a new row of each model draws from.
_SEQUENCE_FOR: dict[type, Callable[[object], str]] = {AuthAccount: _account_sequence}
for _name, _spec in SEQUENCES.items():
    if _spec.model is not AuthAccount:
        _SEQUENCE_FOR[_spec.model] = lambda _target, _name=_name: _name


def _highest_in_use(conn: Connection, spec: SequenceSpec) -> int:
    """Largest number already stored with this prefix, so a new counter starts after it."""
    column = getattr(spec.model, spec.column)
    pattern = re.compile(re.escape(spec.prefix) + r"(\d+)$")
    values = conn.execute(select(column).where(column.like(spec.prefix + "%"))).scalars()
    return max((int(m.group(1)) for v in values if v and (m := pattern.match(v))), default=0)


def _next_value(conn: Connection, name: str) -> tuple[str, int, int]:
    table = IdSequence.__table__
    bump = update(table).where(table.c.name == name).values(current_value=table.c.current_value + 1, updated_at=func.now())
    if conn.execute(bump).rowcount == 0:
        # Counter row missing (e.g. create_all made the table before the migration seeded it).
        spec = SEQUENCES[name]
        conn.execute(insert(table).values(
            name=name, prefix=spec.prefix, width=spec.width,
            current_value=_highest_in_use(conn, spec) + 1, updated_at=func.now(),
        ))
    row = conn.execute(select(table.c.prefix, table.c.width, table.c.current_value).where(table.c.name == name)).one()
    return row.prefix, row.width, row.current_value


def next_number_on(conn: Connection, name: str) -> str:
    prefix, width, value = _next_value(conn, name)
    return f"{prefix}{value:0{width}d}"


def next_number(db: Session, name: str) -> str:
    """Reserve the next readable number of a kind, e.g. next_number(db, "INVOICE")."""
    return next_number_on(db.connection(), name)


def _fill_number(mapper, connection: Connection, target) -> None:
    spec_name = _SEQUENCE_FOR[type(target)](target)
    spec = SEQUENCES[spec_name]
    explicit = getattr(target, spec.column, None)
    if not explicit:
        setattr(target, spec.column, next_number_on(connection, spec_name))
        return
    # A number set by hand: move the counter past it so it is never handed out again.
    match = re.fullmatch(re.escape(spec.prefix) + r"(\d+)", explicit)
    if match:
        table = IdSequence.__table__
        connection.execute(
            update(table)
            .where(table.c.name == spec_name, table.c.current_value < int(match.group(1)))
            .values(current_value=int(match.group(1)), updated_at=func.now())
        )


for _model in _SEQUENCE_FOR:
    event.listen(_model, "before_insert", _fill_number)
