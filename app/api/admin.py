import secrets
import string
from fastapi import APIRouter, Depends, Form, HTTPException, File, UploadFile, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import AdminAccount
from app.core.security import hash_password
from app.core.templates import templates
from app.models.admin import Admin
from app.models.auth import AccountType, AuthAccount
from app.models.client import Client
from app.models.filing_document import FilingDocument
from app.models.staff import Staff, StaffRole
from app.models.tax_filing import TaxFiling
from app.services.storage_service import generate_uuid, save_document_file, save_profile_picture

router = APIRouter(prefix="/admin", tags=["admin"])


def _generate_temp_password(length: int = 10) -> str:
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
    return "".join(secrets.choice(alphabet) for _ in range(length))


@router.get("/dashboard", response_class=HTMLResponse, name="admin_dashboard")
def admin_dashboard(request: Request, account: AdminAccount, db: Session = Depends(get_db)):
    admin = db.query(Admin).filter(Admin.account_id == account.id).first()
    
    # Calculate filing status counts dynamically
    total_filings = db.query(TaxFiling).count()
    submitted_count = db.query(TaxFiling).filter(TaxFiling.status == "pending_review").count()
    in_review_count = db.query(TaxFiling).filter(TaxFiling.status == "in_progress").count()
    payment_pending_count = db.query(TaxFiling).filter(TaxFiling.status == "payment_pending").count()
    completed_count = db.query(TaxFiling).filter(TaxFiling.status == "complete").count()

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
            "description": f"Filing status: {f.status.replace('_', ' ').title()} ({f.get_case_id(db)})",
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
    formatted_clients = []
    for c in clients:
        filing_count = db.query(TaxFiling).filter(TaxFiling.client_id == c.id).count()
        email = c.account.email if c.account else "N/A"
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

    client_count = db.query(Client).count() + 1
    client_number = f"CLI-{client_count:08d}"
    account_number = f"ACC-{client_count:08d}"

    auth_acc = AuthAccount(
        account_number=account_number,
        email=email.strip().lower(),
        password_hash=hashed_pwd,
        account_type=AccountType.CLIENT,
        is_active=True,
        is_verified=True,
    )
    db.add(auth_acc)
    db.flush()

    new_client = Client(
        client_number=client_number,
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
        "client_number": client_number,
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

    staff_count = db.query(Staff).count() + 1
    staff_number = f"STF-{staff_count:08d}"
    account_number = f"ACC-STF-{staff_count:08d}"

    try:
        staff_role_enum = StaffRole(role.upper())
    except ValueError:
        staff_role_enum = StaffRole.INITIATOR

    auth_acc = AuthAccount(
        account_number=account_number,
        email=email.strip().lower(),
        password_hash=hashed_pwd,
        account_type=AccountType.STAFF,
        is_active=True,
        is_verified=True,
    )
    db.add(auth_acc)
    db.flush()

    new_staff = Staff(
        staff_number=staff_number,
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
        "staff_number": staff_number,
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

    filing_count = db.query(TaxFiling).count() + 1
    case_number = f"FLI_{filing_count:07d}"

    email = client.account.email if client.account else f"{client.first_name.lower()}@example.com"

    new_filing = TaxFiling(
        client_id=client.id,
        case_number=case_number,
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
        "case_number": case_number,
        "redirect_url": f"/admin/cases/{new_filing.id}"
    }


@router.post("/quick-action/upload-document", response_class=JSONResponse)
async def quick_upload_document(
    account: AdminAccount,
    filing_id: str = Form(...),
    doc_type: str = Form("general"),
    file: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    filing = db.query(TaxFiling).filter(TaxFiling.id == filing_id).first()
    if not filing:
        raise HTTPException(status_code=404, detail="Filing not found.")

    category_map = {
        "w2": "Income",
        "1099": "Income",
        "id_proof": "Personal",
        "general": "Other"
    }
    category = category_map.get(doc_type.lower(), "Other")

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
    doc_type: str = Form("general"),
    file: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        raise HTTPException(status_code=404, detail="Client not found.")

    filing_count = db.query(TaxFiling).count() + 1
    case_number = f"FLI_{filing_count:07d}"

    email = client.account.email if client.account else f"{client.first_name.lower()}@example.com"

    new_filing = TaxFiling(
        client_id=client.id,
        case_number=case_number,
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

    category_map = {
        "w2": "Income",
        "1099": "Income",
        "id_proof": "Personal",
        "general": "Other"
    }
    category = category_map.get(doc_type.lower(), "Other")

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
        "message": f"Filing '{case_number}' created and initial document uploaded!",
        "filing_id": new_filing.id,
        "redirect_url": f"/admin/cases/{new_filing.id}"
    }
