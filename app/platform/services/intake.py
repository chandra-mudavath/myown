"""Client intake without the New filing form (client feedback, Oct 2026).

The client fills Personal Info, Tax Notes, Upload Documents and Bank Details for a tax year. The year's case
(TaxFiling) is created behind the scenes the first time they save Tax Notes, upload a document or save Bank
Details, from their profile, so staff see it in Registered as before. Tax Notes Yes/No answers drive the
"Documents we need from you" checklist.
"""
import re as _re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from sqlalchemy.orm import Session
from starlette.datastructures import FormData

from app.modules.client.models.client import Client
from app.platform.models.auth import AuthAccount
from app.platform.models.filing_document import FilingDocument
from app.platform.models.filing_intake import FilingIntake
from app.platform.models.tax_filing import TaxFiling

TAX_NOTES = "tax_notes"
BANK = "bank"
RESIDENCY = "residency"      # Personal Info → Visa & residency
DEPENDENTS = "dependents"    # Personal Info → Dependents: {"items": [...]}
FBAR = "fbar"                # FBAR: {"filer": {...}, "accounts": [...], "no_separate", "no_joint"}
EFILE = "efile_auth"         # E-File Authorization (Form 8879); submitted_at = when they signed

# Checklist: (question field, answer that asks for documents, what to upload, document type codes that satisfy it).
# A document uploaded under any of the codes ticks the item off.
CHECKLIST = [
    ("inc_rental", "yes", "Rental income & expenses statement", ["RENTAL_STATEMENT"]),
    ("inc_rental", "yes", "Property tax receipts for the rental", ["PROPERTY_TAX"]),
    ("inc_india_salary", "yes", "Monthly salary payslips or Form-16", ["INDIA_FORM_16"]),
    ("inc_interest", "yes", "Form 1099-INT (or Form-16 for Indian interest)", ["FORM_1099_INT", "INDIA_FORM_16"]),
    ("inc_dividend", "yes", "Form 1099-DIV", ["FORM_1099_DIV"]),
    ("inc_distributions", "yes", "Form 1099-SA or Form 1099-R", ["FORM_1099_SA", "FORM_1099_R"]),
    ("inc_prev_return", "no", "Copy of last year's tax return", ["PRIOR_RETURN"]),
    ("exp_student_visa", "yes", "Visa / I-94 / I-20", ["VISA_I94"]),
    ("exp_tuition", "yes", "Form 1098-T", ["FORM_1098_T"]),
    ("exp_edu_loan", "yes", "Form 1098-E", ["FORM_1098_E"]),
    ("exp_mortgage", "yes", "Form 1098 (mortgage interest)", ["FORM_1098"]),
    ("exp_hsa_ira", "yes", "Form 5498-SA or 5498 IRA statement", ["FORM_5498_SA"]),
    ("exp_stock_sales", "yes", "Broker statement / Form 1099-B", ["FORM_1099_B"]),
    ("exp_foreign_mf", "yes", "Foreign mutual funds summary (the Excel sheet)", ["MF_SUMMARY"]),
    ("exp_medical", "yes", "Medical bills", ["MEDICAL_RECEIPTS"]),
    ("exp_charity", "yes", "Donation receipts", ["CHARITY_RECEIPTS"]),
    ("exp_health_ins", "yes", "Form 1099-HC (only if you lived in Massachusetts)", ["STATE_HEALTH_1099_HC"]),
    ("exp_marketplace", "yes", "Form 1095-A", ["FORM_1095_A"]),
]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def filing_for(db: Session, client: Client, year: int) -> TaxFiling | None:
    return (db.query(TaxFiling).filter(TaxFiling.client_id == client.id, TaxFiling.tax_year == year)
            .order_by(TaxFiling.created_at.desc()).first())


def ensure_filing(db: Session, client: Client, account: AuthAccount, year: int) -> TaxFiling:
    """The client's case for the year, created from their profile if it doesn't exist yet."""
    filing = filing_for(db, client, year)
    if filing:
        return filing
    filing = TaxFiling(
        client_id=client.id, tax_year=year, filing_type="individual", status="pending_review",
        first_name=client.first_name, last_name=client.last_name or "", email=account.email, phone=client.phone,
        date_of_birth=client.date_of_birth, address_line_1=client.address_line_1, address_line_2=client.address_line_2,
        city=client.city, state_province=client.state_province, postal_code=client.postal_code, country=client.country,
    )  # case_number is filled in on insert
    db.add(filing)
    db.flush()
    return filing


def section(db: Session, filing: TaxFiling | None, name: str) -> FilingIntake | None:
    if not filing:
        return None
    return db.query(FilingIntake).filter(FilingIntake.filing_id == filing.id, FilingIntake.section == name).first()


def form_to_dict(form: FormData, skip: set[str] = frozenset()) -> dict:
    """Form fields as a dict: names ending in [] become lists (repeatable rows), everything else a string."""
    data: dict = {}
    for key in form.keys():
        if key in skip:
            continue
        if key.endswith("[]"):
            data[key[:-2]] = [str(v).strip()[:500] for v in form.getlist(key)]
        else:
            value = form.get(key)
            data[key] = str(value).strip()[:4000] if value is not None else ""
    return data


def save_section(db: Session, filing: TaxFiling, name: str, data: dict, account_id: str, *, merge: bool = False,
                 submitted: bool = False) -> FilingIntake:
    row = section(db, filing, name)
    if not row:
        row = FilingIntake(filing_id=filing.id, section=name, data={})
        db.add(row)
    row.data = {**(row.data or {}), **data} if merge else data
    row.updated_by_account_id = account_id
    if submitted:
        row.submitted_at = _now()
    filing.updated_at = _now()
    db.commit()
    return row


def checklist(db: Session, filing: TaxFiling | None) -> list[dict] | None:
    """Documents the Tax Notes answers ask for, each ticked off when a matching document is uploaded.
    None when the client hasn't saved Tax Notes yet."""
    notes = section(db, filing, TAX_NOTES)
    if not notes:
        return None
    answers = notes.data or {}
    uploaded: dict[str, list[FilingDocument]] = {}
    for d in (db.query(FilingDocument).filter(FilingDocument.filing_id == filing.id, FilingDocument.is_latest.is_(True),
                                              FilingDocument.document_type.isnot(None))):
        uploaded.setdefault(d.document_type, []).append(d)
    items = []
    for field, when, label, codes in CHECKLIST:
        if answers.get(field) != when:
            continue
        docs = [d for c in codes for d in uploaded.get(c, []) if d.status != "rejected_reupload_requested"]
        items.append({"label": label, "codes": codes, "done": bool(docs), "files": docs})
    return items


# ── For staff: the saved answers in readable form ────────────────────────────

QUESTIONS = [
    ("married", "Married (as of Dec 31)"),
    ("inc_stayed_usa", "Stayed in the USA during the year"),
    ("inc_rental", "Owns rental property"),
    ("inc_india_salary", "Salary income from India"),
    ("inc_interest", "Interest income"),
    ("inc_dividend", "Dividend income"),
    ("inc_distributions", "HSA / IRA / 401(k) distributions"),
    ("inc_prev_return", "Filed last year's return with us"),
    ("exp_student_visa", "Student on F, M, J or Q visa"),
    ("exp_tuition", "Paid tuition / higher-education expenses"),
    ("exp_edu_loan", "Paid education loan interest"),
    ("exp_mortgage", "Paid US mortgage interest"),
    ("exp_hsa_ira", "Contributed to HSA or IRA"),
    ("exp_stock_sales", "Stock sales or carry-forward losses"),
    ("exp_foreign_mf", "Owns mutual funds outside the US"),
    ("exp_medical", "Out-of-pocket medical expenses"),
    ("exp_charity", "Charitable contributions"),
    ("exp_health_ins", "Covered by health insurance"),
    ("exp_marketplace", "Marketplace insurance (Form 1095-A)"),
]


def notes_summary(data: dict) -> dict:
    """{"answers": [{"label", "answer", "details": ["field: value", …]}], "income_notes", "expenses_notes"}."""
    rows = []
    for key, label in QUESTIONS:
        answer = data.get(key)
        if answer not in ("yes", "no"):
            continue
        details = []
        for field, value in data.items():
            if not field.startswith(key + "_") or field == key:
                continue
            values = [v for v in (value if isinstance(value, list) else [value]) if v]
            if values:
                details.append(f"{field[len(key) + 1:].replace('_', ' ').capitalize()}: {', '.join(values)}")
        rows.append({"label": label, "answer": answer, "details": details})
    return {"answers": rows, "income_notes": data.get("income_notes", ""), "expenses_notes": data.get("expenses_notes", "")}


def masked(number: str | None) -> str:
    return f"•••• {number[-4:]}" if number and len(number) >= 4 else (number or "")


# ── Personal Info, FBAR and e-file forms (were UI only until Oct 2026) ───────


MAX_DEPENDENTS = 15
MAX_FBAR_ACCOUNTS = 50
NATIONALITIES = ("Indian", "US citizen", "Other")
VISAS = ("H1B", "H4", "L1", "L2", "F1", "F1 – OPT", "J1", "Green card", "Other")
RELATIONSHIPS = ("Son", "Daughter", "Parent", "Other")
DEP_ID_TYPES = ("ssn", "itin", "applying")
FILER_TYPES = ("Individual", "Partnership", "Corporation", "Fiduciary")
ACCOUNT_TYPES = ("Bank", "Securities", "Other")
_PIN = _re.compile(r"^(?!00000)\d{5}$")
_DATE = _re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _clean(form, name: str, limit: int = 120) -> str:
    return " ".join(str(form.get(name) or "").split())[:limit]


def _pick(form, name: str, allowed: tuple[str, ...]) -> str:
    value = _clean(form, name)
    return value if value in allowed else ""


def _date(form, name: str) -> str:
    value = _clean(form, name, 10)
    return value if _DATE.match(value) else ""


def _yes_no(form, name: str) -> str:
    return _pick(form, name, ("yes", "no"))


def residency_from_form(form) -> tuple[dict, str | None]:
    visa_count = _clean(form, "visa_count", 1)
    if visa_count not in ("1", "2", "3"):
        return {}, "Choose how many visas you held during the year."
    return {"nationality": _pick(form, "nationality", NATIONALITIES), "visa_count": visa_count,
            "visa_type": _pick(form, "visa_type", VISAS), "first_entry": _date(form, "first_entry"),
            "days_183": _yes_no(form, "days_183")}, None


def dependent_from_form(form) -> tuple[dict, str | None]:
    first, last = _clean(form, "dep_first", 40), _clean(form, "dep_last", 40)
    if not first or not last:
        return {}, "Enter the dependent's first and last name."
    months = _clean(form, "dep_months", 2)
    if months and not (months.isdigit() and 0 <= int(months) <= 12):
        return {}, "Months lived with you must be between 0 and 12."
    return {"first_name": first, "last_name": last, "relationship": _pick(form, "dep_rel", RELATIONSHIPS) or "Other",
            "date_of_birth": _date(form, "dep_dob"), "id_type": _pick(form, "dep_id", DEP_ID_TYPES) or "ssn",
            "months": months}, None


def fbar_filer_from_form(form, saved: dict) -> tuple[dict, str | None]:
    filer_type = _pick(form, "filer_type", FILER_TYPES)
    first, last = _clean(form, "first_name", 40), _clean(form, "last_name", 40)
    if not filer_type or not first or not last:
        return {}, "Choose the type of filer and enter your first and last name."
    has_tin = _yes_no(form, "has_tin") or "yes"
    tin = _re.sub(r"\D", "", str(form.get("tin") or ""))
    if has_tin == "yes":
        tin = tin or saved.get("tin", "")  # blank keeps the number saved earlier
        if len(tin) != 9:
            return {}, "Enter your 9-digit US taxpayer ID (SSN or ITIN)."
    else:
        tin = ""
    return {"filer_type": filer_type, "has_tin": has_tin, "tin": tin,
            "fid_type": _pick(form, "fid_type", ("passport", "other")) or "passport", "fid_number": _clean(form, "fid_number", 40),
            "fid_country": _clean(form, "fid_country", 60), "dob": _date(form, "dob"),
            "first_name": first, "middle_name": _clean(form, "middle_name", 40), "last_name": last,
            "address": _clean(form, "address", 200), "city": _clean(form, "city", 80), "state": _clean(form, "state", 80),
            "zip": _clean(form, "zip", 20), "country": _clean(form, "country", 60),
            "many_accounts": _yes_no(form, "many_accounts") or "no"}, None


def fbar_account_from_form(form, kind: str) -> tuple[dict, str | None]:
    """One foreign account from Part II (kind "sep") or Part III (kind "joint")."""
    p = kind
    acct = {"kind": kind, "type": _pick(form, f"{p}_type", ACCOUNT_TYPES), "institution": _clean(form, f"{p}_institution"),
            "number": _clean(form, f"{p}_number", 40), "address": _clean(form, f"{p}_address", 300)}
    try:
        max_value = Decimal(str(form.get(f"{p}_max") or "").replace(",", "").strip())
    except InvalidOperation:
        max_value = Decimal(-1)
    if not (acct["type"] and acct["institution"] and acct["number"] and acct["address"]) or max_value < 0:
        return {}, "Fill in every field for the account, including its maximum value."
    acct["max_value"] = str(max_value.quantize(Decimal("0.01")))
    if kind == "sep":
        acct["owner"] = _pick(form, "sep_owner", ("Taxpayer", "Spouse"))
        if not acct["owner"]:
            return {}, "Choose whether the account is the taxpayer's or the spouse's."
    else:
        count = _clean(form, "joint_count", 3)
        acct.update(joint_count=count, joint_last=_clean(form, "joint_last", 40),
                    joint_tin=_re.sub(r"\D", "", str(form.get("joint_tin") or ""))[:9], joint_fid=_clean(form, "joint_fid", 40))
        if not (count.isdigit() and int(count) >= 1) or not acct["joint_last"] or not acct["joint_tin"]:
            return {}, "Enter the number of joint owners, the principal owner's last name and US TIN."
    return acct, None


def efile_from_form(form) -> tuple[dict, str | None]:
    pin, spouse = _clean(form, "taxpayer_pin", 5), _clean(form, "spouse_pin", 5)
    if not _PIN.match(pin) or (spouse and not _PIN.match(spouse)):
        return {}, "Each PIN must be 5 digits and not all zeros."
    if form.get("consent_efile") is None:
        return {}, "Tick the declaration to authorize e-filing."
    return {"taxpayer_pin": pin, "spouse_pin": spouse, "consent": "yes"}, None


def fbar_summary(data: dict | None) -> dict | None:
    """FBAR for screens: account numbers and TINs masked."""
    if not data:
        return None
    filer = dict(data.get("filer") or {})
    filer["tin_masked"] = masked(filer.get("tin"))
    accounts = [{**a, "index": i, "number_masked": masked(a.get("number")), "joint_tin_masked": masked(a.get("joint_tin"))}
                for i, a in enumerate(data.get("accounts") or [])]  # index: position for the Remove button
    return {"filer": filer, "separate": [a for a in accounts if a["kind"] == "sep"],
            "joint": [a for a in accounts if a["kind"] == "joint"], "no_separate": data.get("no_separate"),
            "no_joint": data.get("no_joint")}

