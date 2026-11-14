"""Estimated Tax Summary (client feedback, Nov 2026).

A Preparer, Reviewer or Manager enters, for the case's tax year, the refund / (amount due) before and after tax
planning for the federal return and each state return. The client sees them on Tax Summary as a small table:

    Refund/(Due)   Before planning   After planning   Benefits
    Federal                ($184)          $4,598       $4,782
    Total                  ($184)          $4,598       $4,782

Benefits = after - before, so a due of $184 that becomes a refund of $4,598 is a benefit of $4,782. Amounts are
in USD; a due is a negative number (typed as -184 or (184)). Next to the table, "Fee details" lists the services
added to the case. Saved as the case's "tax_summary" intake section (encrypted like the other sections).
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from sqlalchemy.orm import Session

from app.modules.staff.models.staff import StaffRole
from app.platform.models.billing import CaseService
from app.platform.models.tax_filing import TaxFiling
from app.platform.services import intake

SECTION = "tax_summary"
EDITOR_ROLES = (StaffRole.PREPARER, StaffRole.REVIEWER, StaffRole.MANAGER)
MAX_STATES = 6
CENT = Decimal("0.01")
_LIMIT = Decimal("100000000")  # $100M either way: a typo guard, not a business rule


def can_edit(staff) -> bool:
    return bool(staff) and staff.role in EDITOR_ROLES


def _amount(raw: str, label: str, column: str) -> tuple[Decimal | None, str | None]:
    text = str(raw or "").replace(",", "").replace("$", "").strip()
    if not text:
        return None, f"Enter the {column} amount for {label} (use 0 if there is none)."
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1].strip()
    try:
        value = Decimal(text).quantize(CENT)
    except InvalidOperation:
        return None, f"The {column} amount for {label} isn't a number."
    value = -value if negative else value
    if abs(value) >= _LIMIT:
        return None, f"The {column} amount for {label} is too large."
    return value, None


def from_form(form) -> tuple[dict, str | None]:
    """The rows the staff form sent: Federal first, then up to MAX_STATES named state rows. Blank state rows are
    skipped; a state row needs a name and both amounts."""
    labels, befores, afters = form.getlist("label[]"), form.getlist("before[]"), form.getlist("after[]")
    rows = []
    for i, (label, before, after) in enumerate(zip(labels, befores, afters)):
        label = " ".join(str(label or "").split())[:40]
        if i == 0:
            label = "Federal"
        elif not label and not str(before).strip() and not str(after).strip():
            continue
        elif not label:
            return {}, "Give each state row a name, e.g. California."
        b, err = _amount(before, label, "before planning")
        if err:
            return {}, err
        a, err = _amount(after, label, "after planning")
        if err:
            return {}, err
        rows.append({"label": label, "before": str(b), "after": str(a)})
    if not rows:
        return {}, "Enter the federal amounts."
    if len(rows) > MAX_STATES + 1:
        return {}, f"Add up to {MAX_STATES} state returns."
    note = " ".join(str(form.get("note") or "").split())[:300]
    return {"rows": rows, "note": note}, None


def save(db: Session, filing: TaxFiling, data: dict, account_id: str) -> None:
    intake.save_section(db, filing, SECTION, data, account_id, submitted=True)


def view(db: Session, filing: TaxFiling | None) -> dict | None:
    """Rows with Decimal amounts and benefits, the totals, and when it was last updated; None if not entered yet."""
    row = intake.section(db, filing, SECTION)
    if not row or not (row.data or {}).get("rows"):
        return None
    rows = []
    for r in row.data["rows"]:
        before, after = Decimal(r["before"]), Decimal(r["after"])
        rows.append({"label": r["label"], "before": before, "after": after, "benefit": after - before})
    total = {key: sum((r[key] for r in rows), Decimal("0")) for key in ("before", "after", "benefit")}
    return {"rows": rows, "total": total, "note": row.data.get("note", ""), "updated_at": row.updated_at,
            "raw": row.data["rows"]}


def fee_details(db: Session, filing: TaxFiling | None) -> dict:
    """The services added to the case (Fee details next to the summary), with their total."""
    if not filing:
        return {"lines": [], "total": Decimal("0")}
    lines = db.query(CaseService).filter(CaseService.case_id == filing.id).order_by(CaseService.created_at).all()
    return {"lines": [{"name": line.service.name if line.service else "Service", "quantity": line.quantity,
                       "unit_price": line.unit_price, "total": line.total_amount} for line in lines],
            "total": sum((line.total_amount for line in lines), Decimal("0"))}

