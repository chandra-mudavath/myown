"""Admin Settings: tax years, case stages, document types, chat query topics and service prices.

Every change is validated here and recorded in audit_logs with its old and new values.
Functions return an error message, or None when the change was saved.
Case stage *codes* never change (code and stored cases depend on them); only name and order do.
"""
import re
from datetime import date
from decimal import Decimal, InvalidOperation

from sqlalchemy.orm import Session

from app.platform.models.billing import Service
from app.platform.models.lookups import CaseStage, ChatQueryTopic, DocumentType, TaxYear
from app.platform.models.system import AuditLog


def _audit(db: Session, actor_id: str, action: str, entity_type: str, entity_id, old: dict | None, new: dict | None) -> None:
    db.add(AuditLog(actor_account_id=actor_id, action=action, entity_type=entity_type, entity_id=str(entity_id),
                    old_values=old, new_values=new))


def _clean_name(value: str | None, max_len: int, what: str = "Name") -> tuple[str | None, str | None]:
    name = " ".join((value or "").split())
    if not name:
        return None, f"{what} is required."
    if len(name) > max_len:
        return None, f"{what} can be at most {max_len} characters."
    return name, None


def _code_from(name: str, existing: set[str], max_len: int = 30) -> str:
    base = re.sub(r"[^A-Z0-9]+", "_", name.upper()).strip("_")[:max_len] or "ITEM"
    code, n = base, 2
    while code in existing:
        suffix = f"_{n}"
        code, n = base[: max_len - len(suffix)] + suffix, n + 1
    return code


def overview(db: Session) -> dict:
    return {
        "tax_years": db.query(TaxYear).order_by(TaxYear.year.desc()).all(),
        "stages": db.query(CaseStage).order_by(CaseStage.sort_order, CaseStage.id).all(),
        "document_types": db.query(DocumentType).order_by(DocumentType.category, DocumentType.sort_order, DocumentType.name).all(),
        "chat_topics": db.query(ChatQueryTopic).order_by(ChatQueryTopic.sort_order, ChatQueryTopic.id).all(),
        "services": db.query(Service).order_by(Service.name).all(),
    }


# ── Tax years ─────────────────────────────────────────────────────────────────

def add_tax_year(db: Session, actor_id: str, year: str, deadline: str | None) -> str | None:
    try:
        y = int(year)
    except (TypeError, ValueError):
        return "Enter a year such as 2026."
    if not 2000 <= y <= 2100:
        return "Enter a year between 2000 and 2100."
    if db.query(TaxYear).filter(TaxYear.year == y).first():
        return f"Tax year {y} already exists."
    due, err = _parse_date(deadline)
    if err:
        return err
    top = db.query(TaxYear).order_by(TaxYear.sort_order.desc()).first()
    ty = TaxYear(year=y, name=str(y), is_open=True, is_default=False, is_active=True, filing_deadline=due,
                 sort_order=(top.sort_order + 1) if top else 1)
    db.add(ty)
    db.flush()
    _audit(db, actor_id, "tax_year_added", "tax_year", ty.id, None, {"year": y, "filing_deadline": deadline or None})
    db.commit()
    return None


def update_tax_year(db: Session, actor_id: str, tax_year_id: int, is_open: bool, is_default: bool, deadline: str | None) -> str | None:
    ty = db.get(TaxYear, tax_year_id)
    if not ty:
        return "Tax year not found."
    due, err = _parse_date(deadline)
    if err:
        return err
    if is_default and not is_open:
        return "The default year must be open."
    if ty.is_default and not is_default:
        return "Pick another year as the default instead of clearing it here."
    old = {"is_open": ty.is_open, "is_default": ty.is_default, "filing_deadline": str(ty.filing_deadline or "") or None}
    if is_default and not ty.is_default:
        db.query(TaxYear).filter(TaxYear.id != ty.id, TaxYear.is_default.is_(True)).update({"is_default": False})
    ty.is_open, ty.is_default, ty.filing_deadline = is_open, is_default, due
    if is_open and not ty.is_active:
        ty.is_active = True
    _audit(db, actor_id, "tax_year_updated", "tax_year", ty.id, old,
           {"is_open": is_open, "is_default": is_default, "filing_deadline": deadline or None})
    db.commit()
    return None


def _parse_date(value: str | None) -> tuple[date | None, str | None]:
    if not value:
        return None, None
    try:
        return date.fromisoformat(value), None
    except ValueError:
        return None, "Enter the deadline as a date."


# ── Case stages (rename + reorder only) ───────────────────────────────────────

def rename_stage(db: Session, actor_id: str, stage_id: int, name: str) -> str | None:
    stage = db.get(CaseStage, stage_id)
    if not stage:
        return "Stage not found."
    clean, err = _clean_name(name, 80, "Stage name")
    if err:
        return err
    if clean != stage.name:
        _audit(db, actor_id, "stage_renamed", "case_stage", stage.code, {"name": stage.name}, {"name": clean})
        stage.name = clean
        db.commit()
    return None


def move_stage(db: Session, actor_id: str, stage_id: int, direction: str) -> str | None:
    stages = db.query(CaseStage).order_by(CaseStage.sort_order, CaseStage.id).all()
    idx = next((i for i, s in enumerate(stages) if s.id == stage_id), None)
    if idx is None:
        return "Stage not found."
    other = idx - 1 if direction == "up" else idx + 1
    if not 0 <= other < len(stages):
        return None
    # renumber 1..n so equal sort_orders can't make a swap a no-op
    for i, s in enumerate(stages, start=1):
        s.sort_order = i
    a, b = stages[idx], stages[other]
    a.sort_order, b.sort_order = b.sort_order, a.sort_order
    _audit(db, actor_id, "stage_moved", "case_stage", a.code, {"position": idx + 1}, {"position": other + 1})
    db.commit()
    return None


# ── Document types ────────────────────────────────────────────────────────────

def update_document_type(db: Session, actor_id: str, type_id: int, name: str, is_active: bool, max_file_mb: str | None) -> str | None:
    dt = db.get(DocumentType, type_id)
    if not dt:
        return "Document type not found."
    clean, err = _clean_name(name, 120, "Document type name")
    if err:
        return err
    limit = None
    if max_file_mb not in (None, ""):
        try:
            limit = int(max_file_mb)
        except ValueError:
            return "Max size must be a whole number of MB."
        if not 1 <= limit <= 100:
            return "Max size must be between 1 and 100 MB."
    old = {"name": dt.name, "is_active": dt.is_active, "max_file_mb": dt.max_file_mb}
    dt.name, dt.is_active, dt.max_file_mb = clean, is_active, limit
    _audit(db, actor_id, "document_type_updated", "document_type", dt.code, old,
           {"name": clean, "is_active": is_active, "max_file_mb": limit})
    db.commit()
    return None


# ── Chat query topics ─────────────────────────────────────────────────────────

def add_chat_topic(db: Session, actor_id: str, name: str) -> str | None:
    clean, err = _clean_name(name, 80, "Topic name")
    if err:
        return err
    if db.query(ChatQueryTopic).filter(ChatQueryTopic.name == clean).first():
        return f'A topic called "{clean}" already exists.'
    codes = {c for (c,) in db.query(ChatQueryTopic.code).all()}
    top = db.query(ChatQueryTopic).order_by(ChatQueryTopic.sort_order.desc()).first()
    topic = ChatQueryTopic(code=_code_from(clean, codes), name=clean, is_active=True,
                           sort_order=(top.sort_order + 1) if top else 1)
    db.add(topic)
    db.flush()
    _audit(db, actor_id, "chat_topic_added", "chat_query_topic", topic.code, None, {"name": clean})
    db.commit()
    return None


def update_chat_topic(db: Session, actor_id: str, topic_id: int, name: str, is_active: bool) -> str | None:
    topic = db.get(ChatQueryTopic, topic_id)
    if not topic:
        return "Topic not found."
    clean, err = _clean_name(name, 80, "Topic name")
    if err:
        return err
    old = {"name": topic.name, "is_active": topic.is_active}
    topic.name, topic.is_active = clean, is_active
    _audit(db, actor_id, "chat_topic_updated", "chat_query_topic", topic.code, old, {"name": clean, "is_active": is_active})
    db.commit()
    return None


# ── Services and prices ───────────────────────────────────────────────────────

def _parse_price(value: str | None) -> tuple[Decimal | None, str | None]:
    try:
        price = Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, AttributeError):
        return None, "Enter the price as a number, e.g. 150 or 149.99."
    if price < 0 or price > Decimal("1000000"):
        return None, "Price must be between 0 and 1,000,000."
    return price.quantize(Decimal("0.01")), None


def add_service(db: Session, actor_id: str, name: str, price: str, description: str | None) -> str | None:
    clean, err = _clean_name(name, 120, "Service name")
    if err:
        return err
    amount, err = _parse_price(price)
    if err:
        return err
    if db.query(Service).filter(Service.name == clean).first():
        return f'A service called "{clean}" already exists.'
    codes = {c for (c,) in db.query(Service.service_code).all()}
    svc = Service(service_code=_code_from(clean, codes), name=clean, base_price=amount,
                  description=(description or "").strip() or None, is_active=True)
    db.add(svc)
    db.flush()
    _audit(db, actor_id, "service_added", "service", svc.id, None, {"name": clean, "base_price": str(amount)})
    db.commit()
    return None


def update_service(db: Session, actor_id: str, service_id: str, name: str, price: str, is_active: bool) -> str | None:
    svc = db.get(Service, service_id)
    if not svc:
        return "Service not found."
    clean, err = _clean_name(name, 120, "Service name")
    if err:
        return err
    amount, err = _parse_price(price)
    if err:
        return err
    old = {"name": svc.name, "base_price": str(svc.base_price), "is_active": svc.is_active}
    svc.name, svc.base_price, svc.is_active = clean, amount, is_active
    _audit(db, actor_id, "service_updated", "service", svc.id, old,
           {"name": clean, "base_price": str(amount), "is_active": is_active})
    db.commit()
    return None
