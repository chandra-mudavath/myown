from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import StaffAccount
from app.core.templates import templates
from app.models.client import Client
from app.models.filing_document import FilingDocument
from app.models.staff import Staff
from app.models.tax_filing import TaxFiling
from app.services.storage_service import save_profile_picture

router = APIRouter(prefix="/staff", tags=["staff"])


@router.get("/dashboard", response_class=HTMLResponse, name="staff_dashboard")
def staff_dashboard(request: Request, account: StaffAccount, db: Session = Depends(get_db)):
    staff = db.query(Staff).filter(Staff.account_id == account.id).first()
    
    # Calculate counts dynamically based on filings in DB
    total_my_cases = db.query(TaxFiling).count()
    new_cases_count = db.query(TaxFiling).filter(TaxFiling.status == "pending_review").count()
    info_pending_count = db.query(TaxFiling).filter(TaxFiling.status == "info_pending").count()
    
    # Docs pending: filings with status 'docs_pending' or with 0 documents
    docs_pending_count = db.query(TaxFiling).filter(TaxFiling.status == "docs_pending").count()

    dashboard_counts = {
        "my_cases": total_my_cases,
        "new_cases": new_cases_count,
        "info_pending": info_pending_count,
        "docs_pending": docs_pending_count,
    }

    # Fetch recent activity across filings based on updated_at / created_at
    recent_filings = db.query(TaxFiling).order_by(TaxFiling.updated_at.desc()).limit(5).all()
    recent_activity = []
    for f in recent_filings:
        updated_time = f.updated_at or f.created_at
        time_str = updated_time.strftime("%b %d, %H:%M") if updated_time else "Recently"
        recent_activity.append({
            "id": f.id,
            "case_id": f.get_case_id(db),
            "description": f"Filing updated for {f.first_name} {f.last_name} ({f.status.replace('_', ' ').title()})",
            "time": time_str,
            "tone": "teal" if f.status == "complete" else ("amber" if "pending" in f.status else "blue"),
        })

    return templates.TemplateResponse(
        "staff/index.html",
        {
            "request": request,
            "account": account,
            "staff": staff,
            "dashboard_counts": dashboard_counts,
            "recent_activity": recent_activity,
            "app_name": settings.APP_NAME,
        },
    )


@router.get("/cases/my", response_class=HTMLResponse, name="staff_my_cases")
def staff_my_cases(
    request: Request,
    account: StaffAccount,
    q: str | None = None,
    stage: str | None = None,
    year: str | None = None,
    db: Session = Depends(get_db)
):
    staff = db.query(Staff).filter(Staff.account_id == account.id).first()
    
    query = db.query(TaxFiling)
    
    # Apply search filter (Case ID, Case Number, or Client Name)
    if q and q.strip():
        search_term = f"%{q.strip()}%"
        query = query.filter(
            (TaxFiling.id.ilike(search_term)) |
            (TaxFiling.case_number.ilike(search_term)) |
            (TaxFiling.first_name.ilike(search_term)) |
            (TaxFiling.last_name.ilike(search_term)) |
            ((TaxFiling.first_name + " " + TaxFiling.last_name).ilike(search_term))
        )
    
    # Apply stage filter
    if stage and stage.strip():
        query = query.filter(TaxFiling.status == stage.strip())
        
    # Apply year filter
    if year and year.isdigit():
        query = query.filter(TaxFiling.tax_year == int(year))

    filings = query.order_by(TaxFiling.created_at.desc()).all()
    formatted_cases = []
    for f in filings:
        doc_count = db.query(FilingDocument).filter(FilingDocument.filing_id == f.id).count()
        formatted_cases.append({
            "id": f.id,
            "case_id": f.get_case_id(db),
            "client_id": f.client_id,
            "client_name": f"{f.first_name} {f.last_name}",
            "tax_year": f.tax_year,
            "filing_type": f.filing_type,
            "stage": f.status.replace("_", " ").title(),
            "doc_count": doc_count,
        })

    return templates.TemplateResponse(
        "staff/cases/my_cases.html",
        {
            "request": request,
            "account": account,
            "staff": staff,
            "cases": formatted_cases,
            "app_name": settings.APP_NAME,
            "active_nav": "my_cases",
            "page_title": "My Assigned Cases",
            "q": q or "",
            "selected_stage": stage or "",
            "selected_year": year if (year and year.isdigit()) else "",
        },
    )


@router.get("/cases", response_class=HTMLResponse, name="staff_all_cases")
def staff_all_cases(
    request: Request,
    account: StaffAccount,
    q: str | None = None,
    stage: str | None = None,
    year: str | None = None,
    db: Session = Depends(get_db)
):
    staff = db.query(Staff).filter(Staff.account_id == account.id).first()
    
    query = db.query(TaxFiling)
    
    # Apply search filter
    if q and q.strip():
        search_term = f"%{q.strip()}%"
        query = query.filter(
            (TaxFiling.id.ilike(search_term)) |
            (TaxFiling.case_number.ilike(search_term)) |
            (TaxFiling.first_name.ilike(search_term)) |
            (TaxFiling.last_name.ilike(search_term)) |
            ((TaxFiling.first_name + " " + TaxFiling.last_name).ilike(search_term))
        )
    
    # Apply stage filter
    if stage and stage.strip():
        query = query.filter(TaxFiling.status == stage.strip())
        
    # Apply year filter
    if year and year.isdigit():
        query = query.filter(TaxFiling.tax_year == int(year))

    filings = query.order_by(TaxFiling.created_at.desc()).all()
    formatted_cases = []
    for f in filings:
        doc_count = db.query(FilingDocument).filter(FilingDocument.filing_id == f.id).count()
        formatted_cases.append({
            "id": f.id,
            "case_id": f.get_case_id(db),
            "client_id": f.client_id,
            "client_name": f"{f.first_name} {f.last_name}",
            "tax_year": f.tax_year,
            "filing_type": f.filing_type,
            "stage": f.status.replace("_", " ").title(),
            "doc_count": doc_count,
        })

    return templates.TemplateResponse(
        "staff/cases/my_cases.html",
        {
            "request": request,
            "account": account,
            "staff": staff,
            "cases": formatted_cases,
            "app_name": settings.APP_NAME,
            "active_nav": "all_cases",
            "page_title": "All Tax Cases",
            "q": q or "",
            "selected_stage": stage or "",
            "selected_year": str(year) if year else "",
        },
    )


@router.get("/cases/{case_id}", response_class=HTMLResponse, name="staff_case_detail")
def staff_case_detail(case_id: str, request: Request, account: StaffAccount, db: Session = Depends(get_db)):
    staff = db.query(Staff).filter(Staff.account_id == account.id).first()
    
    # Lookup filing by ID, case_number, or prefix
    filing = db.query(TaxFiling).filter(
        (TaxFiling.id == case_id) | (TaxFiling.case_number == case_id)
    ).first()
    if not filing:
        filings = db.query(TaxFiling).all()
        for f in filings:
            if f.get_case_id(db).upper() == case_id.upper() or f.id[:8].upper() == case_id.upper():
                filing = f
                break

    formatted_case = None
    if filing:
        formatted_case = {
            "id": filing.id,
            "case_id": filing.get_case_id(db),
            "client_name": f"{filing.first_name} {filing.last_name}",
            "tax_year": filing.tax_year,
            "filing_type": filing.filing_type,
            "stage": filing.status.replace("_", " ").title(),
            "record": filing
        }

    return templates.TemplateResponse(
        "staff/cases/detail.html",
        {"request": request, "account": account, "staff": staff, "case": formatted_case, "app_name": settings.APP_NAME},
    )


@router.get("/cases/{case_id}/documents", response_class=HTMLResponse, name="staff_case_documents")
def staff_case_documents(case_id: str, request: Request, account: StaffAccount, db: Session = Depends(get_db)):
    staff = db.query(Staff).filter(Staff.account_id == account.id).first()
    
    # Lookup filing by ID, case_number, or prefix
    filing = db.query(TaxFiling).filter(
        (TaxFiling.id == case_id) | (TaxFiling.case_number == case_id)
    ).first()
    if not filing:
        filings = db.query(TaxFiling).all()
        for f in filings:
            if f.get_case_id(db).upper() == case_id.upper() or f.id[:8].upper() == case_id.upper():
                filing = f
                break

    formatted_case = None
    documents = []
    if filing:
        formatted_case = {
            "id": filing.id,
            "case_id": filing.get_case_id(db),
            "client_name": f"{filing.first_name} {filing.last_name}",
            "tax_year": filing.tax_year,
            "filing_type": filing.filing_type,
            "stage": filing.status.replace("_", " ").title(),
        }
        documents = db.query(FilingDocument).filter(FilingDocument.filing_id == filing.id).all()

    return templates.TemplateResponse(
        "staff/cases/documents.html",
        {
            "request": request,
            "account": account,
            "staff": staff,
            "case": formatted_case,
            "documents": documents,
            "app_name": settings.APP_NAME,
        },
    )


@router.get("/clients", response_class=HTMLResponse, name="staff_clients")
def staff_clients(request: Request, account: StaffAccount, db: Session = Depends(get_db)):
    staff = db.query(Staff).filter(Staff.account_id == account.id).first()
    clients = db.query(Client).all()
    return templates.TemplateResponse(
        "staff/clients/index.html",
        {"request": request, "account": account, "staff": staff, "clients": clients, "app_name": settings.APP_NAME},
    )


@router.get("/clients/{client_id}", response_class=HTMLResponse, name="staff_client_detail")
def staff_client_detail(client_id: str, request: Request, account: StaffAccount, db: Session = Depends(get_db)):
    staff = db.query(Staff).filter(Staff.account_id == account.id).first()
    
    # Lookup client by ID or client_number
    client = db.query(Client).filter(Client.id == client_id).first()
    if not client:
        client = db.query(Client).filter(Client.client_number == client_id).first()

    if not client:
        # Fallback if client record wasn't created separately
        filing = db.query(TaxFiling).filter(TaxFiling.client_id == client_id).first()
        if filing:
            client = Client(
                id=filing.client_id,
                client_number="CLT-00000001",
                first_name=filing.first_name,
                last_name=filing.last_name,
                phone=filing.phone,
                date_of_birth=filing.date_of_birth,
                address_line_1=filing.address_line_1,
                address_line_2=filing.address_line_2,
                city=filing.city,
                state_province=filing.state_province,
                postal_code=filing.postal_code,
                country=filing.country,
            )

    filings = []
    if client:
        filings = db.query(TaxFiling).filter(TaxFiling.client_id == client.id).order_by(TaxFiling.created_at.desc()).all()

    return templates.TemplateResponse(
        "staff/clients/detail.html",
        {
            "request": request,
            "account": account,
            "staff": staff,
            "client": client,
            "filings": filings,
            "app_name": settings.APP_NAME,
        },
    )


@router.get("/notifications", response_class=HTMLResponse, name="staff_notifications")
def staff_notifications(request: Request, account: StaffAccount, db: Session = Depends(get_db)):
    staff = db.query(Staff).filter(Staff.account_id == account.id).first()
    return templates.TemplateResponse(
        "staff/notifications.html",
        {"request": request, "account": account, "staff": staff, "app_name": settings.APP_NAME},
    )


@router.get("/profile", response_class=HTMLResponse, name="staff_profile")
def staff_profile(request: Request, account: StaffAccount, db: Session = Depends(get_db)):
    staff = db.query(Staff).filter(Staff.account_id == account.id).first()
    return templates.TemplateResponse(
        "staff/profile.html",
        {"request": request, "account": account, "staff": staff, "app_name": settings.APP_NAME},
    )


@router.post("/profile/avatar")
def upload_staff_avatar(
    account: StaffAccount,
    file: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    staff = db.query(Staff).filter(Staff.account_id == account.id).first()
    if not staff:
        raise HTTPException(status_code=404, detail="Staff account not found")
    avatar_url = save_profile_picture(file, staff.id, "staff")
    staff.profile_picture = avatar_url
    db.commit()
    return {"message": "Staff profile picture updated successfully!", "avatar_url": avatar_url}
