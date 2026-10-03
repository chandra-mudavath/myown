import secrets
import string
from fastapi import APIRouter, Depends, Form, HTTPException, File, UploadFile, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import AdminAccount
from app.core.security import hash_password
from app.core.templates import templates
from app.modules.admin.models.admin import Admin
from app.platform.models.auth import AccountType, AuthAccount
from app.modules.client.models.client import Client
from app.platform.models.filing_document import FilingDocument
from app.platform.models.lookups import DocumentType
from app.modules.staff.models.staff import Staff, StaffRole
from app.platform.models.tax_filing import TaxFiling
from app.platform.services.catalog import (
    case_document_types,
    case_stages,
    default_tax_year,
    open_tax_years,
    stage_codes_for_client_status,
    stage_labels,
    staff_roles,
    tax_types,
)
from app.platform.services.storage_service import generate_uuid, save_document_file, save_profile_picture

router = APIRouter(prefix="/admin", tags=["admin"])


def _generate_temp_password(length: int = 10) -> str:
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
    return "".join(secrets.choice(alphabet) for _ in range(length))


@router.get("/dashboard", response_class=HTMLResponse, name="admin_dashboard")
def admin_dashboard(request: Request, account: AdminAccount, db: Session = Depends(get_db)):
    admin = db.query(Admin).filter(Admin.account_id == account.id).first()
    
    # Filing status counts, grouped by the client-facing status each stage maps to (case_stages)
    total_filings = db.query(TaxFiling).count()

    def _count_in(codes: list[str]) -> int:
        return db.query(TaxFiling).filter(TaxFiling.status.in_(codes)).count() if codes else 0

    in_progress_codes = stage_codes_for_client_status(db, "In Progress")
    submitted_count = _count_in(stage_codes_for_client_status(db, "Submitted"))
    payment_pending_count = _count_in(["payment_pending"])
    in_review_count = _count_in([c for c in in_progress_codes if c != "payment_pending"])
    completed_count = _count_in(stage_codes_for_client_status(db, "Completed"))
    labels = stage_labels(db)

    # Calculate business overview counts
    total_clients = db.query(Client).count()
    total_staff = db.query(Staff).count()

    dashboard_stats = {
        "submitted": submitted_count,
        "in_review": in_review_count,
        "payment_pending": payment_pending_count,
        "completed": completed_count,
        "total_clients": total_clients,
        "total_filings": total_filings,
        "total_staff": total_staff,
    }

    # Fetch recent activity across filings
    recent_filings = db.query(TaxFiling).order_by(TaxFiling.updated_at.desc()).limit(5).all()
    recent_activity = []
    for f in recent_filings:
        updated_time = f.updated_at or f.created_at
        time_str = updated_time.strftime("%b %d, %H:%M") if updated_time else "Recently"
        recent_activity.append({
            "name": f"{f.first_name} {f.last_name}",
            "description": f"Filing status: {labels.get(f.status, f.status.replace('_', ' ').title())} ({f.get_case_id(db)})",
            "time": time_str,
            "tone": "teal" if f.status == "complete" else ("amber" if "pending" in f.status else "blue"),
            "case_id": f.id
        })

    clients_list = db.query(Client).all()
    filings_list = db.query(TaxFiling).order_by(TaxFiling.created_at.desc()).all()
    formatted_filings = [{"id": f.id, "case_id": f.get_case_id(db), "client_name": f"{f.first_name} {f.last_name}", "tax_year": f.tax_year} for f in filings_list]

    return templates.TemplateResponse(
        "admin/index.html",
        {
            "request": request,
            "account": account,
            "admin": admin,
            "dashboard_stats": dashboard_stats,
            "activity": recent_activity,
            "clients_list": clients_list,
            "filings_list": formatted_filings,
            "app_name": settings.APP_NAME,
        },
    )


@router.post("/profile/avatar")
def upload_admin_avatar(
    account: AdminAccount,
    file: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    admin = db.query(Admin).filter(Admin.account_id == account.id).first()
    if not admin:
        raise HTTPException(status_code=404, detail="Admin account not found")
    avatar_url = save_profile_picture(file, admin.id, "admin")
    admin.profile_picture = avatar_url
    db.commit()
    return {"message": "Admin profile picture updated successfully!", "avatar_url": avatar_url}


@router.get("/clients", response_class=HTMLResponse, name="admin_clients")
def admin_clients_directory(
    request: Request,
    account: AdminAccount,
    q: str | None = None,
    db: Session = Depends(get_db)
):
    admin = db.query(Admin).filter(Admin.account_id == account.id).first()
    
    query = db.query(Client)
    if q and q.strip():
        search_term = f"%{q.strip()}%"
        query = query.filter(
            (Client.client_number.ilike(search_term)) |
            (Client.first_name.ilike(search_term)) |
            (Client.last_name.ilike(search_term)) |
            ((Client.first_name + " " + Client.last_name).ilike(search_term))
        )
    
    clients = query.order_by(Client.created_at.desc()).all()
    client_ids = [c.id for c in clients]
    # One grouped query each for filing counts and emails, instead of two queries per client.
    filing_counts = dict(
        db.query(TaxFiling.client_id, func.count(TaxFiling.id))
        .filter(TaxFiling.client_id.in_(client_ids))
        .group_by(TaxFiling.client_id)
        .all()
    ) if client_ids else {}
    emails = dict(
        db.query(AuthAccount.id, AuthAccount.email)
        .filter(AuthAccount.id.in_([c.account_id for c in clients]))
        .all()
    ) if clients else {}
    formatted_clients = []
    for c in clients:
        filing_count = filing_counts.get(c.id, 0)
        email = emails.get(c.account_id, "N/A")
        formatted_clients.append({
            "id": c.id,
            "client_number": c.client_number,
            "first_name": c.first_name,
            "last_name": c.last_name,
            "email": email,
            "phone": c.phone,
            "filing_count": filing_count,
        })

    return templates.TemplateResponse(
        "admin/clients/index.html",
        {
            "request": request,
            "account": account,
            "admin": admin,
            "clients": formatted_clients,
            "active_nav": "clients",
            "q": q or "",
            "app_name": settings.APP_NAME,
        },
    )


@router.get("/clients/{client_id}", response_class=HTMLResponse, name="admin_client_detail")
def admin_client_detail(
    request: Request,
    client_id: str,
    account: AdminAccount,
    db: Session = Depends(get_db)
):
    admin = db.query(Admin).filter(Admin.account_id == account.id).first()
    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found")

    filings = db.query(TaxFiling).filter(TaxFiling.client_id == client.id).order_by(TaxFiling.created_at.desc()).all()
    formatted_filings = []
    for f in filings:
        formatted_filings.append({
            "id": f.id,
            "case_id": f.get_case_id(db),
            "tax_year": f.tax_year,
            "filing_type": f.filing_type,
            "status": f.status,
            "created_at": f.created_at,
        })

    return templates.TemplateResponse(
        "admin/clients/detail.html",
        {
            "request": request,
            "stage_labels": stage_labels(db),
            "account": account,
            "admin": admin,
            "client": client,
            "filings": formatted_filings,
            "active_nav": "clients",
            "app_name": settings.APP_NAME,
        },
    )


@router.get("/cases", response_class=HTMLResponse, name="admin_cases_queue")
def admin_cases_queue(
    request: Request,
    account: AdminAccount,
    status: str | None = None,
    q: str | None = None,
    db: Session = Depends(get_db)
):
    admin = db.query(Admin).filter(Admin.account_id == account.id).first()

    query = db.query(TaxFiling)

    if status and status.strip():
        query = query.filter(TaxFiling.status == status.strip())

    if q and q.strip():
        search_term = f"%{q.strip()}%"
        query = query.filter(
            (TaxFiling.case_number.ilike(search_term)) |
            (TaxFiling.first_name.ilike(search_term)) |
            (TaxFiling.last_name.ilike(search_term)) |
            ((TaxFiling.first_name + " " + TaxFiling.last_name).ilike(search_term))
        )

    filings = query.order_by(TaxFiling.created_at.desc()).all()
    formatted_filings = []
    for f in filings:
        formatted_filings.append({
            "id": f.id,
            "case_id": f.get_case_id(db),
            "client_name": f"{f.first_name} {f.last_name}",
            "client_id": f.client_id,
            "tax_year": f.tax_year,
            "filing_type": f.filing_type,
            "status": f.status,
            "created_at": f.created_at,
        })

    return templates.TemplateResponse(
        "admin/cases/index.html",
        {
            "request": request,
            "stage_labels": stage_labels(db),
            "stages": case_stages(db),
            "account": account,
            "admin": admin,
            "filings": formatted_filings,
            "active_nav": "queue",
            "selected_status": status or "",
            "q": q or "",
            "app_name": settings.APP_NAME,
        },
    )


@router.get("/cases/{case_id}", response_class=HTMLResponse, name="admin_case_detail")
def admin_case_detail(
    request: Request,
    case_id: str,
    account: AdminAccount,
    db: Session = Depends(get_db)
):
    admin = db.query(Admin).filter(Admin.account_id == account.id).first()
    filing = db.query(TaxFiling).filter(TaxFiling.id == case_id).first()
    if not filing:
        raise HTTPException(status_code=404, detail="Tax filing not found")

    client = db.query(Client).filter(Client.id == filing.client_id).first()
    documents = db.query(FilingDocument).filter(FilingDocument.filing_id == filing.id).all()

    return templates.TemplateResponse(
        "admin/cases/detail.html",
        {
            "request": request,
            "stage_labels": stage_labels(db),
            "account": account,
            "admin": admin,
            "filing": filing,
            "case_number": filing.get_case_id(db),
            "client": client,
            "documents": documents,
            "active_nav": "clients",
            "app_name": settings.APP_NAME,
        },
    )


@router.get("/staff", response_class=HTMLResponse, name="admin_staff")
def admin_staff_directory(
    request: Request,
    account: AdminAccount,
    q: str | None = None,
    db: Session = Depends(get_db)
):
    admin = db.query(Admin).filter(Admin.account_id == account.id).first()
    
    query = db.query(Staff)
    if q and q.strip():
        search_term = f"%{q.strip()}%"
        query = query.filter(
            (Staff.staff_number.ilike(search_term)) |
            (Staff.first_name.ilike(search_term)) |
            (Staff.last_name.ilike(search_term)) |
            ((Staff.first_name + " " + Staff.last_name).ilike(search_term))
        )

    staff_records = query.order_by(Staff.created_at.desc()).all()
    formatted_staff = []
    for s in staff_records:
        email = s.account.email if s.account else "N/A"
        formatted_staff.append({
            "id": s.id,
            "staff_number": s.staff_number,
            "first_name": s.first_name,
            "last_name": s.last_name,
            "email": email,
            "role": s.role,
            "created_at": s.created_at,
        })

    return templates.TemplateResponse(
        "admin/staff/index.html",
        {
            "request": request,
            "account": account,
            "admin": admin,
            "staff_list": formatted_staff,
            "active_nav": "staff",
            "q": q or "",
            "app_name": settings.APP_NAME,
        },
    )


@router.get("/staff/{staff_id}", response_class=HTMLResponse, name="admin_staff_detail")
def admin_staff_detail(
    request: Request,
    staff_id: str,
    account: AdminAccount,
    db: Session = Depends(get_db)
):
    admin = db.query(Admin).filter(Admin.account_id == account.id).first()
    staff_member = db.query(Staff).filter(Staff.id == staff_id).first()
    if not staff_member:
        raise HTTPException(status_code=404, detail="Staff member not found")

    # Fetch all filings available in the system
    all_filings = db.query(TaxFiling).order_by(TaxFiling.created_at.desc()).all()
    formatted_filings = []
    for f in all_filings:
        formatted_filings.append({
            "id": f.id,
            "case_id": f.get_case_id(db),
            "client_name": f"{f.first_name} {f.last_name}",
            "client_id": f.client_id,
            "tax_year": f.tax_year,
            "filing_type": f.filing_type,
            "status": f.status,
            "created_at": f.created_at,
        })

    return templates.TemplateResponse(
        "admin/staff/detail.html",
        {
            "request": request,
            "stage_labels": stage_labels(db),
            "account": account,
            "admin": admin,
            "staff_member": staff_member,
            "filings": formatted_filings,
            "active_nav": "staff",
            "app_name": settings.APP_NAME,
        },
    )


# --------------------------------------------------------------------------
# Quick Actions APIs
# --------------------------------------------------------------------------

@router.get("/api/clients", response_class=JSONResponse)
def get_clients_api(account: AdminAccount, db: Session = Depends(get_db)):
    clients = db.query(Client).all()
    return [{"id": c.id, "client_number": c.client_number, "name": f"{c.first_name} {c.last_name}"} for c in clients]


@router.get("/api/form-options", response_class=JSONResponse)
def get_form_options_api(account: AdminAccount, db: Session = Depends(get_db)):
    """Dropdown options for the quick-action forms, from the lookup tables."""
    return {
        "tax_years": open_tax_years(db),
        "default_tax_year": default_tax_year(db),
        "tax_types": [{"code": t.code, "name": t.name} for t in tax_types(db)],
        "document_types": [{"code": d.code, "name": d.name} for d in case_document_types(db)],
        "staff_roles": [{"code": r.code, "name": r.name} for r in staff_roles(db)],
    }


@router.get("/api/filings", response_class=JSONResponse)
def get_filings_api(account: AdminAccount, db: Session = Depends(get_db)):
    filings = db.query(TaxFiling).order_by(TaxFiling.created_at.desc()).all()
    return [{"id": f.id, "case_id": f.get_case_id(db), "client_name": f"{f.first_name} {f.last_name}", "tax_year": f.tax_year} for f in filings]


@router.post("/quick-action/client", response_class=JSONResponse)
def quick_create_client(
    account: AdminAccount,
    first_name: str = Form(...),
    last_name: str = Form(...),
    email: str = Form(...),
    phone: str = Form(...),
    db: Session = Depends(get_db)
):
    existing_auth = db.query(AuthAccount).filter(AuthAccount.email == email.strip().lower()).first()
    if existing_auth:
        raise HTTPException(status_code=400, detail="An account with this email already exists.")

    temp_password = _generate_temp_password(10)
    hashed_pwd = hash_password(temp_password)

    auth_acc = AuthAccount(
        email=email.strip().lower(),
        password_hash=hashed_pwd,
        account_type=AccountType.CLIENT,
        is_active=True,
        is_verified=True,
    )
    db.add(auth_acc)
    db.flush()

    new_client = Client(
        account_id=auth_acc.id,
        first_name=first_name.strip(),
        last_name=last_name.strip(),
        phone=phone.strip(),
    )
    db.add(new_client)
    db.commit()

    return {
        "success": True,
        "message": "Client created successfully!",
        "client_number": new_client.client_number,
        "email": email.strip().lower(),
        "temp_password": temp_password,
    }


@router.post("/quick-action/staff", response_class=JSONResponse)
def quick_create_staff(
    account: AdminAccount,
    first_name: str = Form(...),
    last_name: str = Form(...),
    email: str = Form(...),
    phone: str | None = Form(None),
    role: str = Form("INITIATOR"),
    db: Session = Depends(get_db)
):
    existing_auth = db.query(AuthAccount).filter(AuthAccount.email == email.strip().lower()).first()
    if existing_auth:
        raise HTTPException(status_code=400, detail="An account with this email already exists.")

    temp_password = _generate_temp_password(10)
    hashed_pwd = hash_password(temp_password)

    try:
        staff_role_enum = StaffRole(role.upper())
    except ValueError:
        staff_role_enum = StaffRole.INITIATOR

    auth_acc = AuthAccount(
        email=email.strip().lower(),
        password_hash=hashed_pwd,
        account_type=AccountType.STAFF,
        is_active=True,
        is_verified=True,
    )
    db.add(auth_acc)
    db.flush()

    new_staff = Staff(
        account_id=auth_acc.id,
        first_name=first_name.strip(),
        last_name=last_name.strip(),
        phone=phone.strip() if phone else None,
        role=staff_role_enum,
    )
    db.add(new_staff)
    db.commit()

    return {
        "success": True,
        "message": "Staff created successfully!",
        "staff_number": new_staff.staff_number,
        "email": email.strip().lower(),
        "temp_password": temp_password,
    }


@router.post("/quick-action/filing", response_class=JSONResponse)
def quick_create_filing(
    account: AdminAccount,
    client_id: str = Form(...),
    tax_year: int = Form(...),
    filing_type: str = Form("individual"),
    db: Session = Depends(get_db)
):
    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found.")

    email = client.account.email if client.account else f"{client.first_name.lower()}@example.com"

    new_filing = TaxFiling(
        client_id=client.id,
        tax_year=tax_year,
        filing_type=filing_type,
        first_name=client.first_name,
        last_name=client.last_name or "",
        email=email,
        phone=client.phone,
        status="pending_review",
    )
    db.add(new_filing)
    db.commit()

    return {
        "success": True,
        "message": "New filing created successfully!",
        "filing_id": new_filing.id,
        "case_number": new_filing.case_number,
        "redirect_url": f"/admin/cases/{new_filing.id}"
    }


@router.post("/quick-action/upload-document", response_class=JSONResponse)
async def quick_upload_document(
    account: AdminAccount,
    filing_id: str = Form(...),
    doc_type: str = Form("OTHER"),  # document_types.code
    file: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    filing = db.query(TaxFiling).filter(TaxFiling.id == filing_id).first()
    if not filing:
        raise HTTPException(status_code=404, detail="Filing not found.")

    doc_type_row = db.query(DocumentType).filter(DocumentType.code == doc_type).first()
    category = doc_type_row.category if doc_type_row else "Other"

    group_id = generate_uuid()
    stored_name, relative_path, mime_type, file_size = save_document_file(
        file=file,
        client_id=filing.client_id,
        tax_year=filing.tax_year,
        filing_id=filing.id,
        category=category,
        version=1,
        document_name=file.filename,
    )

    doc_record = FilingDocument(
        filing_id=filing.id,
        document_group_id=group_id,
        category=category,
        document_name=file.filename or "uploaded_document",
        original_filename=file.filename or "uploaded_document",
        stored_filename=stored_name,
        file_path=relative_path,
        file_size=file_size,
        mime_type=mime_type,
        version=1,
        is_latest=True,
        status="pending_review",
        uploaded_by_type="ADMIN",
        uploaded_by_id=account.id,
    )
    db.add(doc_record)
    db.commit()

    return {
        "success": True,
        "message": f"Document '{file.filename}' uploaded successfully!",
        "document_id": doc_record.id,
        "case_number": filing.get_case_id(db)
    }


@router.post("/quick-action/new-filing-with-doc", response_class=JSONResponse)
async def quick_create_filing_with_doc(
    account: AdminAccount,
    client_id: str = Form(...),
    tax_year: int = Form(...),
    filing_type: str = Form("individual"),
    doc_type: str = Form("OTHER"),  # document_types.code
    file: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found.")

    email = client.account.email if client.account else f"{client.first_name.lower()}@example.com"

    new_filing = TaxFiling(
        client_id=client.id,
        tax_year=tax_year,
        filing_type=filing_type,
        first_name=client.first_name,
        last_name=client.last_name or "",
        email=email,
        phone=client.phone,
        status="pending_review",
    )
    db.add(new_filing)
    db.flush()

    doc_type_row = db.query(DocumentType).filter(DocumentType.code == doc_type).first()
    category = doc_type_row.category if doc_type_row else "Other"

    group_id = generate_uuid()
    stored_name, relative_path, mime_type, file_size = save_document_file(
        file=file,
        client_id=client.id,
        tax_year=tax_year,
        filing_id=new_filing.id,
        category=category,
        version=1,
        document_name=file.filename,
    )

    doc_record = FilingDocument(
        filing_id=new_filing.id,
        document_group_id=group_id,
        category=category,
        document_name=file.filename or "uploaded_document",
        original_filename=file.filename or "uploaded_document",
        stored_filename=stored_name,
        file_path=relative_path,
        file_size=file_size,
        mime_type=mime_type,
        version=1,
        is_latest=True,
        status="pending_review",
        uploaded_by_type="ADMIN",
        uploaded_by_id=account.id,
    )
    db.add(doc_record)
    db.commit()

    return {
        "success": True,
        "message": f"Filing '{new_filing.case_number}' created and initial document uploaded!",
        "filing_id": new_filing.id,
        "redirect_url": f"/admin/cases/{new_filing.id}"
    }
