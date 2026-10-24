"""Admin Settings page. Each form posts to its own route and comes back to the matching section."""
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import AdminAccount
from app.core.templates import templates
from app.modules.admin.services import system_settings as svc
from app.modules.admin.services.profile_service import current_admin
from app.platform.services import admin_signup, email_service

router = APIRouter(prefix="/admin/settings", tags=["admin"])

SECTIONS = [("tax-years", "Tax years"), ("stages", "Case stages"), ("document-types", "Document types"),
            ("chat-topics", "Chat topics"), ("services", "Services & prices"), ("admin-signup", "Admin sign-up link")]


def _back(section: str, err: str | None, saved: str) -> RedirectResponse:
    query = urlencode({"error": err} if err else {"saved": saved})
    return RedirectResponse(f"/admin/settings?{query}#{section}", status_code=303)


@router.get("", response_class=HTMLResponse, name="admin_settings")
def admin_settings(request: Request, account: AdminAccount, db: Session = Depends(get_db), saved: str = "", error: str = ""):
    return templates.TemplateResponse(request, "admin/settings.html", {
        "request": request, "account": account, "admin": current_admin(db, account), "app_name": settings.APP_NAME,
        "active_nav": "settings", "sections": SECTIONS, "max_upload_mb": settings.MAX_UPLOAD_SIZE_MB, "saved": saved, "error": error, **svc.overview(db),
        "admin_signup_url": email_service.public_url(request, admin_signup.PATH + key) if (key := admin_signup.current_key(db)) else None,
    })


@router.post("/tax-years")
def tax_year_add(account: AdminAccount, db: Session = Depends(get_db), year: str = Form(""), filing_deadline: str = Form("")):
    return _back("tax-years", svc.add_tax_year(db, account.id, year, filing_deadline), f"Tax year {year} added.")


@router.post("/tax-years/{tax_year_id}")
def tax_year_update(tax_year_id: int, account: AdminAccount, db: Session = Depends(get_db),
                    is_open: bool = Form(False), is_default: bool = Form(False), filing_deadline: str = Form("")):
    err = svc.update_tax_year(db, account.id, tax_year_id, is_open, is_default, filing_deadline)
    return _back("tax-years", err, "Tax year saved.")


@router.post("/stages/{stage_id}")
def stage_rename(stage_id: int, account: AdminAccount, db: Session = Depends(get_db), name: str = Form("")):
    return _back("stages", svc.rename_stage(db, account.id, stage_id, name), "Stage renamed.")


@router.post("/stages/{stage_id}/move")
def stage_move(stage_id: int, account: AdminAccount, db: Session = Depends(get_db), direction: str = Form("up")):
    return _back("stages", svc.move_stage(db, account.id, stage_id, direction), "Stage order saved.")


@router.post("/document-types/{type_id}")
def document_type_update(type_id: int, account: AdminAccount, db: Session = Depends(get_db),
                         name: str = Form(""), is_active: bool = Form(False), max_file_mb: str = Form("")):
    err = svc.update_document_type(db, account.id, type_id, name, is_active, max_file_mb)
    return _back("document-types", err, "Document type saved.")


@router.post("/chat-topics")
def chat_topic_add(account: AdminAccount, db: Session = Depends(get_db), name: str = Form("")):
    return _back("chat-topics", svc.add_chat_topic(db, account.id, name), "Topic added.")


@router.post("/chat-topics/{topic_id}")
def chat_topic_update(topic_id: int, account: AdminAccount, db: Session = Depends(get_db),
                      name: str = Form(""), is_active: bool = Form(False)):
    return _back("chat-topics", svc.update_chat_topic(db, account.id, topic_id, name, is_active), "Topic saved.")


@router.post("/services")
def service_add(account: AdminAccount, db: Session = Depends(get_db),
                name: str = Form(""), base_price: str = Form(""), description: str = Form("")):
    return _back("services", svc.add_service(db, account.id, name, base_price, description), "Service added.")


@router.post("/services/{service_id}")
def service_update(service_id: str, account: AdminAccount, db: Session = Depends(get_db),
                   name: str = Form(""), base_price: str = Form(""), is_active: bool = Form(False)):
    err = svc.update_service(db, account.id, service_id, name, base_price, is_active)
    return _back("services", err, "Service saved.")


@router.post("/admin-signup")
def admin_signup_generate(account: AdminAccount, db: Session = Depends(get_db)):
    """Make a new admin-only sign-up link; the previous one stops working (client feedback, Oct 2026)."""
    admin_signup.generate(db, account.id)
    return _back("admin-signup", None, "New admin sign-up link generated. The previous link no longer works.")
