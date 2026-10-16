"""Demo data for showing the portals to a client.

Adds, without touching existing records:
  - 4 staff, one per case role (Initiator, Preparer, Reviewer, Manager)
  - 2 admins
  - a small service price list (only services that don't exist yet)
  - 9 clients, each with one case at a different point of the workflow. Billing starts once
    the return is filed, so everything from "completed" on has been through every stage:
      started filing -> in progress -> completed (e-filed, IRS acknowledgment awaited)
      -> acknowledged (accepted by the IRS) -> invoice drafted -> invoice generated
      -> invoice sent -> payment pending (overdue) -> payment completed

Safe to run again: anything whose email / name already exists is skipped.
--rebuild redoes the demo clients' cases in place (same case numbers), e.g. after the
scenarios below change. Every demo login uses the password below.

    ..\\env\\Scripts\\python.exe seed_demo_data.py [--rebuild]
"""
import os
import sys
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import app.db_models  # noqa: F401,E402 — registers models and the readable-number hooks
from app.core.config import settings  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.modules.admin.models.admin import Admin  # noqa: E402
from app.modules.client.models.client import Client  # noqa: E402
from app.modules.staff.models.staff import Staff, StaffRole  # noqa: E402
from app.platform.models.auth import AccountType, AuthAccount  # noqa: E402
from app.platform.models.billing import CaseService, Invoice, Payment, Service  # noqa: E402
from app.platform.models.case_workflow import Acknowledgment, CaseAssignment, CaseStageHistory, IrsSubmission  # noqa: E402
from app.platform.models.filing_document import DocumentComment, FilingDocument  # noqa: E402
from app.platform.models.lookups import StaffRoleLookup  # noqa: E402
from app.platform.models.system import AuditLog  # noqa: E402
from app.platform.models.tax_filing import TaxFiling  # noqa: E402

PASSWORD = "Demo@1234"
CENT = Decimal("0.01")
NOW = datetime.now(timezone.utc)
TODAY = NOW.date()


def ago(days: float, hour: int = 10) -> datetime:
    """A time `days` before today, at a working hour, so lists and feeds look natural."""
    d = TODAY - timedelta(days=int(days))
    return datetime.combine(d, time(hour, (int(days * 37) % 60)), tzinfo=timezone.utc)


# ── People ──────────────────────────────────────────────────────────────────

STAFF = [
    # email, first, last, job title, role, phone
    ("emily.carter@covrenttax.com", "Emily", "Carter", "Client Onboarding Lead", StaffRole.INITIATOR, "+1 (415) 555-0141"),
    ("rahul.verma@covrenttax.com", "Rahul", "Verma", "Senior Tax Preparer", StaffRole.PREPARER, "+1 (415) 555-0142"),
    ("sophia.nguyen@covrenttax.com", "Sophia", "Nguyen", "Tax Reviewer, CPA", StaffRole.REVIEWER, "+1 (415) 555-0143"),
    ("david.miller@covrenttax.com", "David", "Miller", "Tax Operations Manager", StaffRole.MANAGER, "+1 (415) 555-0144"),
]

ADMINS = [
    ("olivia.bennett.admin@covrenttax.com", "Olivia", "Bennett"),
    ("arjun.mehta.admin@covrenttax.com", "Arjun", "Mehta"),
]

SERVICES = [
    ("Federal Individual Return (1040)", "250.00", "Preparation and e-filing of the federal Form 1040."),
    ("State Return", "120.00", "One state income tax return."),
    ("FBAR / FinCEN 114", "90.00", "Report of foreign bank and financial accounts."),
    ("Foreign Tax Credit (Form 1116)", "75.00", "Credit for income tax paid to another country."),
    ("Dependent & Credits Review", "40.00", "Child tax credit, dependent care and education credits."),
]

# Stages each scenario passes through, in order; the last one is where the case sits now.
PATH_START = ["pending_review"]
PATH_IN_PROGRESS = PATH_START + ["docs_pending", "docs_received", "preparation"]
# An e-filed (Form 8879) return; Data Models/schema-map: only 8879 cases get an IRS submission + acknowledgment.
PATH_DONE = PATH_IN_PROGRESS + ["review", "draft_uploading", "client_review", "form_8879", "e_filing",
                                "state_filing", "complete"]
NO_INCOME_TAX_STATES = {"Alaska", "Florida", "Nevada", "New Hampshire", "South Dakota", "Tennessee", "Texas",
                        "Washington", "Wyoming"}  # no state return to file or bill
# Cases where the client re-uploaded a rejected W-2, so the version history shows a superseded v1.
REUPLOAD_SCENARIOS = {"invoice_sent", "completed"}

# client fields, case fields, scenario
CLIENTS = [
    {
        "email": "michael.johnson@gmail.com", "first": "Michael", "last": "Johnson", "phone": "+1 (312) 555-0101",
        "dob": date(1986, 4, 12), "city": "Chicago", "state": "Illinois", "postal": "60614", "addr": "2150 N Lincoln Ave",
        "residency": "us_citizen", "filing_status": "single", "income": ["w2_employment", "interest"],
        "deductions": ["student_loan_interest"], "special": [],
        "scenario": "started", "path": PATH_START, "created": 2, "done": 2,
    },
    {
        "email": "priya.raman@gmail.com", "first": "Priya", "last": "Raman", "phone": "+1 (408) 555-0102",
        "dob": date(1990, 9, 3), "city": "San Jose", "state": "California", "postal": "95112", "addr": "88 E San Fernando St",
        "residency": "resident_alien", "filing_status": "married_jointly", "income": ["w2_employment", "dividends", "foreign"],
        "deductions": ["mortgage_interest", "property_taxes"], "special": ["foreign_accounts"],
        "spouse": ("Karthik", "Raman"), "scenario": "in_progress", "path": PATH_IN_PROGRESS, "created": 12, "done": 1,
    },
    {
        "email": "james.wilson@gmail.com", "first": "James", "last": "Wilson", "phone": "+1 (617) 555-0103",
        "dob": date(1979, 1, 22), "city": "Boston", "state": "Massachusetts", "postal": "02116", "addr": "500 Boylston St",
        "residency": "us_citizen", "filing_status": "married_jointly", "income": ["w2_employment", "capital_gains"],
        "deductions": ["mortgage_interest", "charitable_contributions"], "special": [],
        "spouse": ("Laura", "Wilson"), "scenario": "invoice_draft", "path": PATH_DONE, "created": 40, "done": 4,
    },
    {
        "email": "ananya.iyer@gmail.com", "first": "Ananya", "last": "Iyer", "phone": "+1 (206) 555-0104",
        "dob": date(1993, 6, 30), "city": "Seattle", "state": "Washington", "postal": "98109", "addr": "400 Fairview Ave N",
        "residency": "resident_alien", "filing_status": "single", "income": ["w2_employment", "foreign"],
        "deductions": ["retirement_contributions"], "special": ["foreign_accounts", "foreign_income"],
        "scenario": "invoice_generated", "path": PATH_DONE, "created": 45, "done": 5,
    },
    {
        "email": "robert.garcia@gmail.com", "first": "Robert", "last": "Garcia", "phone": "+1 (512) 555-0105",
        "dob": date(1982, 11, 8), "city": "Austin", "state": "Texas", "postal": "78701", "addr": "301 Congress Ave",
        "residency": "us_citizen", "filing_status": "head_of_household", "income": ["w2_employment", "self_employment"],
        "deductions": ["child_dependent_care"], "special": [],
        "dependents": [("Mia", "Garcia", "daughter", "2016-03-14")],
        "scenario": "invoice_sent", "path": PATH_DONE, "created": 50, "done": 7,
    },
    {
        "email": "sneha.kapoor@gmail.com", "first": "Sneha", "last": "Kapoor", "phone": "+1 (201) 555-0106",
        "dob": date(1988, 2, 17), "city": "Jersey City", "state": "New Jersey", "postal": "07302", "addr": "77 Hudson St",
        "residency": "resident_alien", "filing_status": "married_jointly", "income": ["w2_employment", "interest", "rental"],
        "deductions": ["mortgage_interest", "property_taxes"], "special": ["multiple_states"],
        "spouse": ("Vikram", "Kapoor"), "scenario": "payment_pending", "path": PATH_DONE, "created": 60, "done": 25,
    },
    {
        "email": "daniel.thompson@gmail.com", "first": "Daniel", "last": "Thompson", "phone": "+1 (303) 555-0107",
        "dob": date(1975, 7, 19), "city": "Denver", "state": "Colorado", "postal": "80202", "addr": "1600 Glenarm Pl",
        "residency": "us_citizen", "filing_status": "married_jointly", "income": ["w2_employment", "retirement"],
        "deductions": ["medical_expenses", "charitable_contributions"], "special": [],
        "spouse": ("Rachel", "Thompson"), "scenario": "payment_completed", "path": PATH_DONE, "created": 70, "done": 30,
    },
    {
        "email": "fatima.khan@gmail.com", "first": "Fatima", "last": "Khan", "phone": "+1 (713) 555-0108",
        "dob": date(1991, 12, 1), "city": "Houston", "state": "Texas", "postal": "77002", "addr": "1001 Fannin St",
        "residency": "resident_alien", "filing_status": "single", "income": ["w2_employment", "foreign"],
        "deductions": ["education_expenses"], "special": ["foreign_accounts"],
        "scenario": "acknowledged", "path": PATH_DONE, "created": 34, "done": 3,
    },
    {
        "email": "william.brown@gmail.com", "first": "William", "last": "Brown", "phone": "+1 (404) 555-0109",
        "dob": date(1968, 5, 25), "city": "Atlanta", "state": "Georgia", "postal": "30303", "addr": "191 Peachtree St NE",
        "residency": "us_citizen", "filing_status": "married_jointly", "income": ["w2_employment", "dividends", "capital_gains"],
        "deductions": ["mortgage_interest", "charitable_contributions", "retirement_contributions"], "special": [],
        "spouse": ("Helen", "Brown"), "dependents": [("Ethan", "Brown", "son", "2009-08-02")],
        "scenario": "completed", "path": PATH_DONE, "created": 30, "done": 1,
    },
]

# Who is on the case, by how far it has got.
TEAM_BY_SCENARIO = {
    "started": [],
    "in_progress": ["INITIATOR", "PREPARER"],
}
FULL_TEAM = ["INITIATOR", "PREPARER", "REVIEWER", "MANAGER"]

# Services billed per scenario: (service name, quantity)
LINES = {
    "default": [("Federal Individual Return (1040)", 1), ("State Return", 1)],
    "foreign": [("Federal Individual Return (1040)", 1), ("State Return", 1), ("FBAR / FinCEN 114", 1),
                ("Foreign Tax Credit (Form 1116)", 1)],
    "family": [("Federal Individual Return (1040)", 1), ("State Return", 1), ("Dependent & Credits Review", 1)],
    "two_states": [("Federal Individual Return (1040)", 1), ("State Return", 2)],
}


# ── Files ───────────────────────────────────────────────────────────────────

def _pdf_bytes(title: str, lines: list[str]) -> bytes:
    """A one-page PDF with a few lines of text, so document previews open."""
    def esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    text = ["BT", "/F1 18 Tf", "72 740 Td", f"({esc(title)}) Tj", "/F1 11 Tf"]
    for line in lines:
        text += ["0 -22 Td", f"({esc(line)}) Tj"]
    text.append("ET")
    stream = "\n".join(text).encode("latin-1", "replace")
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def _save_pdf(client: Client, filing: TaxFiling, category: str, name: str, lines: list[str],
              version: int = 1) -> tuple[str, str, int]:
    folder = Path(settings.CLIENT_DOCUMENTS_DIR) / client.id / str(filing.tax_year) / filing.id / category
    folder.mkdir(parents=True, exist_ok=True)
    slug = "".join(c if c.isalnum() or c in "-_." else "_" for c in name)
    stored = f"{slug}_v{version}_demo{len(name):04d}.pdf"
    data = _pdf_bytes(name, [f"{filing.first_name} {filing.last_name}  -  Tax year {filing.tax_year}", *lines,
                             "", "Sample document created for the demo."])
    (folder / stored).write_bytes(data)
    return stored, str(folder / stored).replace("\\", "/"), len(data)


# ── Builders ────────────────────────────────────────────────────────────────

def _account(db, email: str, kind: AccountType, created: datetime) -> AuthAccount:
    acc = AuthAccount(email=email, password_hash=hash_password(PASSWORD), account_type=kind, is_active=True,
                      is_verified=True, email_verified_at=created, created_at=created, updated_at=created)
    db.add(acc)
    db.flush()
    return acc


def _audit(db, actor: str, action: str, entity_type: str, entity_id: str, old, new, at: datetime) -> None:
    db.add(AuditLog(actor_account_id=actor, action=action, entity_type=entity_type, entity_id=entity_id,
                    old_values=old, new_values=new, created_at=at))


def seed_staff(db) -> dict[str, Staff]:
    by_role = {}
    for i, (email, first, last, title, role, phone) in enumerate(STAFF):
        acc = db.query(AuthAccount).filter_by(email=email).first()
        if acc:
            staff = db.query(Staff).filter_by(account_id=acc.id).first()
            print(f"  skip staff  {email} (exists)")
        else:
            created = ago(120 - i * 5)
            acc = _account(db, email, AccountType.STAFF, created)
            staff = Staff(account_id=acc.id, first_name=first, last_name=last, job_title=title, role=role, phone=phone,
                          created_at=created, updated_at=created)
            db.add(staff)
            db.flush()
            print(f"  staff       {email:<32} {role.value}")
        if staff:
            by_role[staff.role.value] = staff
    return by_role


def seed_admins(db) -> list[Admin]:
    admins = []
    for i, (email, first, last) in enumerate(ADMINS):
        acc = db.query(AuthAccount).filter_by(email=email).first()
        if acc:
            admin = db.query(Admin).filter_by(account_id=acc.id).first()
            print(f"  skip admin  {email} (exists)")
        else:
            created = ago(150 - i * 10)
            acc = _account(db, email, AccountType.ADMIN, created)
            admin = Admin(account_id=acc.id, first_name=first, last_name=last, created_at=created, updated_at=created)
            db.add(admin)
            db.flush()
            print(f"  admin       {email}")
        if admin:
            admins.append(admin)
    return admins


def seed_services(db, actor: str) -> dict[str, Service]:
    taken = {c for (c,) in db.query(Service.service_code).all()}
    for name, price, desc in SERVICES:
        if db.query(Service).filter_by(name=name).first():
            continue
        code = "".join(w[0] for w in name.replace("/", " ").replace("(", " ").split() if w[0].isalnum()).upper()[:20]
        n, base = 2, code
        while code in taken:
            code, n = f"{base}{n}", n + 1
        taken.add(code)
        svc = Service(service_code=code, name=name, base_price=Decimal(price), description=desc, is_active=True,
                      created_at=ago(160), updated_at=ago(160))
        db.add(svc)
        db.flush()
        _audit(db, actor, "service_added", "service", svc.id, None, {"name": name, "base_price": price}, ago(160))
        print(f"  service     {name}  ${price}")
    return {s.name: s for s in db.query(Service).all()}


def _documents(db, client: Client, filing: TaxFiling, c: dict, staff: dict[str, Staff], started: datetime) -> None:
    """Uploads that fit the stage: fresh uploads are waiting for review, later ones are accepted."""
    docs = [("Personal", "Passport", ["Passport identity page"]),
            ("Income", f"W-2 - {c['last']} employer", ["Wages, tips, other compensation", "Federal income tax withheld"])]
    if "dividends" in c["income"] or "capital_gains" in c["income"] or "interest" in c["income"]:
        docs.append(("Investments", "1099 - Brokerage statement", ["Dividends, interest and proceeds"]))
    if "mortgage_interest" in c["deductions"]:
        docs.append(("Deductions", "1098 - Mortgage interest", ["Mortgage interest received from borrower"]))
    if c["special"] and any(s.startswith("foreign") for s in c["special"]):
        docs.append(("Foreign_Information", "Foreign bank statements", ["Year-end balances of foreign accounts"]))

    reviewer = staff.get("INITIATOR")
    for i, (category, name, lines) in enumerate(docs):
        stored, path, size = _save_pdf(client, filing, category, name, lines)
        uploaded = started + timedelta(hours=2 + i * 3)
        status, reason = "accepted", None
        if c["scenario"] == "started":
            status = "pending_review"
        elif c["scenario"] == "in_progress" and category == "Foreign_Information":
            status, reason = "rejected_reupload_requested", "The statement is cut off; please upload all pages, including the year-end balance."
        doc = FilingDocument(filing_id=filing.id, category=category, document_name=name, original_filename=f"{name}.pdf",
                             stored_filename=stored, file_path=path, file_size=size, mime_type="application/pdf",
                             version=1, is_latest=True, status=status, rejection_reason=reason,
                             uploaded_by_type="client", uploaded_by_id=client.id, uploaded_at=uploaded)
        if status != "pending_review" and reviewer:
            doc.reviewed_at, doc.reviewed_by_id = uploaded + timedelta(days=1), reviewer.id
        db.add(doc)
        db.flush()
        if reason and reviewer:
            db.add(DocumentComment(filing_id=filing.id, document_group_id=doc.document_group_id, document_id=doc.id,
                                   kind="rejection", body=reason, author_type="staff",
                                   author_account_id=reviewer.account_id,
                                   author_name=f"{reviewer.first_name} {reviewer.last_name}",
                                   created_at=doc.reviewed_at))
        if category == "Income" and c["scenario"] in REUPLOAD_SCENARIOS and reviewer:
            _reupload(db, client, filing, doc, reviewer, lines)


def _reupload(db, client: Client, filing: TaxFiling, v1: FilingDocument, reviewer: Staff, lines: list[str]) -> None:
    """v1 is sent back, the client uploads v2, staff accept it: v1 ends up superseded."""
    name = f"{reviewer.first_name} {reviewer.last_name}"
    reason = "This W-2 is the employee copy (Copy C) and box 12 is cut off. Please upload Copy B with all boxes visible."
    db.add(DocumentComment(filing_id=filing.id, document_group_id=v1.document_group_id, document_id=v1.id,
                           kind="rejection", body=reason, author_type="staff", author_account_id=reviewer.account_id,
                           author_name=name, created_at=v1.reviewed_at))
    uploaded = v1.reviewed_at + timedelta(hours=20)
    stored, path, size = _save_pdf(client, filing, v1.category, v1.document_name, lines, version=2)
    v2 = FilingDocument(filing_id=filing.id, document_group_id=v1.document_group_id, category=v1.category,
                        document_name=v1.document_name, original_filename=f"{v1.document_name} (Copy B).pdf",
                        stored_filename=stored, file_path=path, file_size=size, mime_type="application/pdf",
                        version=2, is_latest=True, status="accepted", uploaded_by_type="client",
                        uploaded_by_id=client.id, uploaded_at=uploaded, reviewed_at=uploaded + timedelta(hours=5),
                        reviewed_by_id=reviewer.id)
    v1.status, v1.rejection_reason, v1.is_latest = "superseded", reason, False
    db.add(v2)
    db.flush()
    db.add(DocumentComment(filing_id=filing.id, document_group_id=v1.document_group_id, document_id=v2.id,
                           kind="reupload", body="Uploaded Copy B with every box showing.", author_type="client",
                           author_account_id=client.account_id, author_name=f"{client.first_name} {client.last_name}",
                           created_at=uploaded))


def _final_documents(db, client: Client, filing: TaxFiling, staff: dict[str, Staff], done: datetime) -> None:
    """A filed case's paperwork: the draft return the firm shared, and the 8879 the client signed."""
    preparer, manager = staff.get("PREPARER"), staff.get("MANAGER")
    for days_before, category, name, lines, by_client in [
        (6, "Other", f"Draft {filing.tax_year} return", ["Form 1040 and schedules for client review"], False),
        (4, "Other", "Signed Form 8879", ["IRS e-file signature authorization, signed by the taxpayer"], True),
        (1, "Other", f"Filed {filing.tax_year} return", ["Final return as e-filed with the IRS"], False),
    ]:
        stored, path, size = _save_pdf(client, filing, category, name, lines)
        uploaded = done - timedelta(days=days_before)
        uploader = None if by_client else (preparer if days_before > 1 else manager)
        db.add(FilingDocument(filing_id=filing.id, category=category, document_name=name, original_filename=f"{name}.pdf",
                              stored_filename=stored, file_path=path, file_size=size, mime_type="application/pdf",
                              version=1, is_latest=True, status="accepted",
                              uploaded_by_type="client" if by_client else "staff",
                              uploaded_by_id=client.id if by_client else uploader.id, uploaded_at=uploaded,
                              reviewed_at=uploaded + timedelta(hours=3), reviewed_by_id=(manager or preparer).id))


def _stage_history(db, filing: TaxFiling, path: list[str], start: datetime, end: datetime,
                   staff: dict[str, Staff]) -> datetime:
    """One history row per move, spread evenly from the case's start until it reached its current stage."""
    span = max((end - start).total_seconds(), 3600)
    step = span / max(len(path) - 1, 1)
    movers = [staff.get(r) for r in ("INITIATOR", "INITIATOR", "INITIATOR", "PREPARER", "REVIEWER", "PREPARER",
                                     "PREPARER", "MANAGER", "MANAGER", "MANAGER", "MANAGER")]
    when = start
    for i, code in enumerate(path):
        when = start + timedelta(seconds=step * i)
        mover = movers[min(i, len(movers) - 1)] if i else None
        db.add(CaseStageHistory(case_id=filing.id, from_stage_code=path[i - 1] if i else None, to_stage_code=code,
                                changed_by_account_id=(mover.account_id if mover else filing.client.account_id),
                                comment=None, created_at=when))
    return when


def _assign(db, filing: TaxFiling, roles: list[str], staff: dict[str, Staff], roles_by_code: dict, actor: str,
            start: datetime) -> None:
    for i, code in enumerate(roles):
        s = staff.get(code)
        if not s:
            continue
        at = start + timedelta(hours=4 + i * 20)
        a = CaseAssignment(case_id=filing.id, staff_id=s.id, role_id=roles_by_code[code].id, assigned_by_account_id=actor,
                           assigned_at=at, status="active")
        db.add(a)
        db.flush()
        _audit(db, actor, "case_assigned", "case_assignment", a.id, None,
               {"case": filing.case_number, "staff_id": s.id, "role": code}, at)


def _invoice(db, filing: TaxFiling, client: Client, kind: str, services: dict[str, Service], actor: str,
             issued_on: date, status: str, due_days: int) -> Invoice:
    no_state = client.state_province in NO_INCOME_TAX_STATES
    lines = [(services[n], q) for n, q in LINES[kind] if n in services and not (no_state and n == "State Return")]
    subtotal = sum((s.base_price * q for s, q in lines), Decimal("0")).quantize(CENT)
    discount = Decimal("25.00") if subtotal >= 500 else Decimal("0")
    total = subtotal - discount
    created = datetime.combine(issued_on, time(11), tzinfo=timezone.utc)
    inv = Invoice(client_id=client.id, case_id=filing.id, subtotal=subtotal, discount_amount=discount,
                  tax_amount=Decimal("0"), total_amount=total, status="draft", invoice_date=issued_on,
                  due_date=issued_on + timedelta(days=due_days), created_at=created)
    db.add(inv)
    db.flush()
    for s, q in lines:
        db.add(CaseService(case_id=filing.id, invoice_id=inv.id, service_id=s.id, quantity=q, unit_price=s.base_price,
                           total_amount=(s.base_price * q).quantize(CENT), created_at=created))
    _audit(db, actor, "invoice_created", "invoice", inv.id, None,
           {"invoice_number": inv.invoice_number, "case": filing.case_number, "total": str(total)}, created)
    if status != "draft":
        inv.status = "issued"
        _audit(db, actor, "invoice_issued", "invoice", inv.id, {"status": "draft"}, {"status": "issued"},
               created + timedelta(hours=1))
    return inv


def _pay(db, inv: Invoice, actor: str, paid_on: date, method: str, ref: str) -> None:
    pay = Payment(invoice_id=inv.id, client_id=inv.client_id, amount=inv.total_amount, payment_method=method,
                  transaction_reference=ref, status="succeeded",
                  paid_at=datetime.combine(paid_on, time(12), tzinfo=timezone.utc),
                  created_at=datetime.combine(paid_on, time(12, 5), tzinfo=timezone.utc))
    db.add(pay)
    db.flush()
    old = inv.status
    inv.status = "paid"
    _audit(db, actor, "payment_recorded", "payment", pay.id, {"invoice_status": old},
           {"payment_number": pay.payment_number, "invoice": inv.invoice_number, "amount": str(inv.total_amount),
            "method": method, "invoice_status": "paid"}, pay.created_at)


def _efile(db, filing: TaxFiling, staff: dict[str, Staff], submitted: datetime, acknowledged: bool) -> None:
    """The e-file submission; with acknowledged=False the IRS hasn't answered yet."""
    by = staff.get("MANAGER") or staff.get("PREPARER")
    sub = IrsSubmission(case_id=filing.id, submitted_by_account_id=by.account_id if by else None,
                        irs_submission_id=f"{filing.tax_year}{submitted:%j}{filing.case_number[-7:]}",
                        status="accepted" if acknowledged else "submitted", submitted_at=submitted,
                        created_at=submitted, updated_at=submitted + timedelta(days=1 if acknowledged else 0))
    db.add(sub)
    db.flush()
    if not acknowledged:
        return
    received = submitted + timedelta(hours=26)
    db.add(Acknowledgment(submission_id=sub.id, recorded_by_account_id=by.account_id if by else None,
                          irs_ack_number=f"{received:%Y%m%d}{filing.case_number[-7:]}", status="accepted",
                          received_at=received, message="Return accepted by the IRS.", created_at=received))


def _wipe_case(db, filing: TaxFiling) -> None:
    """Remove what the seed built on a demo case (stages, team, documents, billing, e-file), keeping the
    client, the login and the case number. Chats and anything else are left alone."""
    invoice_ids = [i for (i,) in db.query(Invoice.id).filter(Invoice.case_id == filing.id)]
    payment_ids = [p for (p,) in db.query(Payment.id).filter(Payment.invoice_id.in_(invoice_ids or [""]))]
    assignment_ids = [a for (a,) in db.query(CaseAssignment.id).filter(CaseAssignment.case_id == filing.id)]
    submission_ids = [s for (s,) in db.query(IrsSubmission.id).filter(IrsSubmission.case_id == filing.id)]
    db.query(AuditLog).filter(AuditLog.entity_id.in_((invoice_ids + payment_ids + assignment_ids) or [""])).delete(
        synchronize_session=False)
    db.query(Payment).filter(Payment.id.in_(payment_ids or [""])).delete(synchronize_session=False)
    db.query(CaseService).filter(CaseService.case_id == filing.id).delete(synchronize_session=False)
    db.query(Invoice).filter(Invoice.id.in_(invoice_ids or [""])).delete(synchronize_session=False)
    db.query(Acknowledgment).filter(Acknowledgment.submission_id.in_(submission_ids or [""])).delete(synchronize_session=False)
    db.query(IrsSubmission).filter(IrsSubmission.case_id == filing.id).delete(synchronize_session=False)
    db.query(CaseAssignment).filter(CaseAssignment.case_id == filing.id).delete(synchronize_session=False)
    db.query(CaseStageHistory).filter(CaseStageHistory.case_id == filing.id).delete(synchronize_session=False)
    db.query(DocumentComment).filter(DocumentComment.filing_id == filing.id).delete(synchronize_session=False)
    for doc in db.query(FilingDocument).filter(FilingDocument.filing_id == filing.id):
        if "_demo" in doc.stored_filename:  # only the sample PDFs this script wrote
            Path(doc.file_path).unlink(missing_ok=True)
    db.query(FilingDocument).filter(FilingDocument.filing_id == filing.id).delete(synchronize_session=False)
    db.flush()


def seed_clients(db, staff: dict[str, Staff], services: dict[str, Service], actor: str, rebuild: bool) -> None:
    roles_by_code = {r.code: r for r in db.query(StaffRoleLookup).all()}
    for c in CLIENTS:
        joined, start = ago(c["created"] + 1, 9), ago(c["created"])
        acc = db.query(AuthAccount).filter_by(email=c["email"]).first()
        client = db.query(Client).filter_by(account_id=acc.id).first() if acc else None
        filing = (db.query(TaxFiling).filter_by(client_id=client.id).order_by(TaxFiling.created_at).first()
                  if client else None)
        if acc and not rebuild:
            print(f"  skip client {c['email']} (exists; --rebuild redoes its case)")
            continue
        if acc and not client:
            print(f"  skip client {c['email']} (login exists but has no client profile)")
            continue
        if not acc:
            acc = _account(db, c["email"], AccountType.CLIENT, joined)
            client = Client(account_id=acc.id, first_name=c["first"], last_name=c["last"], date_of_birth=c["dob"],
                            phone=c["phone"], address_line_1=c["addr"], city=c["city"], state_province=c["state"],
                            postal_code=c["postal"], country="United States", created_at=joined, updated_at=joined)
            db.add(client)
            db.flush()
        acc.created_at = client.created_at = joined

        if filing:
            _wipe_case(db, filing)
        else:
            filing = TaxFiling(client_id=client.id)
            db.add(filing)
        spouse = c.get("spouse")
        for field, value in dict(
            tax_year=2025, filing_type="individual", first_name=c["first"], last_name=c["last"],
            email=c["email"], phone=c["phone"], date_of_birth=c["dob"],
            country_of_citizenship="United States" if c["residency"] == "us_citizen" else "India",
            current_country_of_residence="United States", us_tax_residency_status=c["residency"],
            filing_status=c["filing_status"], filed_us_taxes_before="yes", previous_tax_year_filed=2024,
            address_line_1=c["addr"], city=c["city"], state_province=c["state"], postal_code=c["postal"],
            country="United States",
            spouse_first_name=spouse[0] if spouse else None, spouse_last_name=spouse[1] if spouse else None,
            spouse_us_tax_residency_status=c["residency"] if spouse else None,
            dependents=[{"first_name": f, "last_name": l, "relationship": r, "date_of_birth": d, "student_status": False,
                         "disability_status": False, "months_lived_with": 12} for f, l, r, d in c.get("dependents", [])],
            income_categories=c["income"], deductions_credits=c["deductions"], special_situations=c["special"],
            has_previous_return_copy="yes", received_irs_notice="no", unresolved_tax_issues="no", is_amended_return="no",
            status=c["path"][-1], created_at=start,
        ).items():
            setattr(filing, field, value)
        filing.client = client
        db.flush()

        scenario = c["scenario"]
        done = ago(c["done"], 15) if len(c["path"]) > 1 else start
        _documents(db, client, filing, c, staff, start)
        path = [code for code in c["path"] if not (code == "state_filing" and c["state"] in NO_INCOME_TAX_STATES)]
        _stage_history(db, filing, path, start, done, staff)
        _assign(db, filing, TEAM_BY_SCENARIO.get(scenario, FULL_TEAM), staff, roles_by_code, actor, start)

        note = ""
        if c["path"][-1] == "complete":
            _final_documents(db, client, filing, staff, done)
            acknowledged = scenario != "completed"
            _efile(db, filing, staff, done - timedelta(days=2), acknowledged)
            note = "e-filed, accepted by the IRS" if acknowledged else "e-filed, IRS acknowledgment awaited"

        kind = ("foreign" if any(s.startswith("foreign") for s in c["special"])
                else "two_states" if "multiple_states" in c["special"]
                else "family" if c.get("dependents") else "default")
        billed = done.date() + timedelta(days=1)  # invoices go out once the return is filed
        if scenario == "invoice_draft":
            _invoice(db, filing, client, kind, services, actor, billed, "draft", 15)
            note += "; invoice drafted"
        elif scenario == "invoice_generated":
            _invoice(db, filing, client, kind, services, actor, billed, "issued", 15)
            note += "; invoice generated"
        elif scenario == "invoice_sent":
            inv = _invoice(db, filing, client, kind, services, actor, billed, "issued", 15)
            _audit(db, actor, "invoice_sent", "invoice", inv.id, None,
                   {"invoice_number": inv.invoice_number, "to": c["email"]},
                   datetime.combine(billed, time(14), tzinfo=timezone.utc))
            note += "; invoice sent to client"
        elif scenario == "payment_pending":
            _invoice(db, filing, client, kind, services, actor, billed, "issued", 14)
            note += "; invoice sent, payment overdue"
        elif scenario == "payment_completed":
            inv = _invoice(db, filing, client, kind, services, actor, billed, "issued", 15)
            _pay(db, inv, actor, billed + timedelta(days=6), "card", f"TXN-{inv.invoice_number[-4:]}{c['postal'][-3:]}")
            note += "; invoice paid"

        filing.updated_at = done
        db.flush()
        print(f"  client      {c['email']:<30} {filing.case_number}  {filing.status:<15} {note.lstrip('; ')}")


def main() -> None:
    rebuild = "--rebuild" in sys.argv[1:]
    db = SessionLocal()
    try:
        print("Staff")
        staff = seed_staff(db)
        print("Admins")
        admins = seed_admins(db)
        actor = admins[0].account_id if admins else None
        print("Services")
        services = seed_services(db, actor)
        print("Clients and cases" + (" (rebuilding)" if rebuild else ""))
        seed_clients(db, staff, services, actor, rebuild)
        db.commit()
        print(f"\nDone. Every demo login uses the password: {PASSWORD}")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
