"""Admin-managed option lists, read from the lookup tables.

Pages call these instead of keeping their own hardcoded lists, so adding a stage, tax
year, tax type, document category, document type or staff role is one row in the
database. Only active rows are returned, in each table's sort_order.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.platform.models.lookups import CaseStage, DocumentCategory, DocumentType, StaffRoleLookup, TaxType, TaxYear


def open_tax_years(db: Session) -> list[int]:
    """Years clients can pick, newest first."""
    rows = (
        db.query(TaxYear.year)
        .filter(TaxYear.is_active.is_(True), TaxYear.is_open.is_(True))
        .order_by(TaxYear.year.desc())
        .all()
    )
    return [year for (year,) in rows]


def default_tax_year(db: Session) -> int | None:
    """The year flagged as default, else the newest open year."""
    year = (
        db.query(TaxYear.year)
        .filter(TaxYear.is_active.is_(True), TaxYear.is_open.is_(True), TaxYear.is_default.is_(True))
        .order_by(TaxYear.year.desc())
        .scalar()
    )
    if year is not None:
        return year
    years = open_tax_years(db)
    return years[0] if years else None


def case_stages(db: Session) -> list[CaseStage]:
    """Workflow stages in pipeline order. ``code`` matches tax_filings.status."""
    return (
        db.query(CaseStage)
        .filter(CaseStage.is_active.is_(True))
        .order_by(CaseStage.sort_order, CaseStage.id)
        .all()
    )


def stage_labels(db: Session) -> dict[str, str]:
    """code -> display name for every stage, including retired ones, so old cases still read well."""
    return {code: name for code, name in db.query(CaseStage.code, CaseStage.name).all()}


def stage_codes_for_client_status(db: Session, client_status: str) -> list[str]:
    """All stage codes that show the client a given status (Submitted, In Progress, ...)."""
    rows = db.query(CaseStage.code).filter(CaseStage.client_status == client_status).all()
    return [code for (code,) in rows]


def tax_types(db: Session) -> list[TaxType]:
    return db.query(TaxType).filter(TaxType.is_active.is_(True)).order_by(TaxType.sort_order, TaxType.id).all()


def document_categories(db: Session) -> dict[str, str]:
    """code -> display name, in display order."""
    rows = (
        db.query(DocumentCategory.code, DocumentCategory.name)
        .filter(DocumentCategory.is_active.is_(True))
        .order_by(DocumentCategory.sort_order, DocumentCategory.id)
        .all()
    )
    return dict(rows)


def case_document_types(db: Session) -> list[DocumentType]:
    """Document types a case upload can be filed under."""
    return (
        db.query(DocumentType)
        .filter(DocumentType.is_active.is_(True), DocumentType.applies_to == "CASE")
        .order_by(DocumentType.sort_order, DocumentType.id)
        .all()
    )


def staff_roles(db: Session) -> list[StaffRoleLookup]:
    """Roles that can be given to staff on a case."""
    return (
        db.query(StaffRoleLookup)
        .filter(StaffRoleLookup.is_active.is_(True), StaffRoleLookup.is_case_role.is_(True))
        .order_by(StaffRoleLookup.sort_order, StaffRoleLookup.id)
        .all()
    )
