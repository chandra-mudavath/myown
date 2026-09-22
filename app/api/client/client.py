from typing import List, Optional
from datetime import datetime, timezone
from fastapi import APIRouter, Cookie, Depends, Form, HTTPException, File, Request, UploadFile, status
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import ClientAccount
from app.core.templates import templates
from app.models.client import Client
from app.models.filing_document import FilingDocument
from app.models.tax_filing import TaxFiling
from app.schemas.tax_filing import TaxFilingCreate
from app.services.storage_service import generate_uuid, save_document_file

router = APIRouter(prefix="/client", tags=["client"])


@router.get("/filings", response_class=HTMLResponse, name="my_filings_page")
async def my_filings_page(
    request: Request,
    account: ClientAccount,
    db: Session = Depends(get_db),
    tax_year: str | None = Cookie(None),
):
    current_year = datetime.now(timezone.utc).year
    selected_year = int(tax_year) if tax_year and tax_year.isdigit() else current_year

    client = db.query(Client).filter(Client.account_id == account.id).first()
    display_name = client.first_name if client else account.email.split("@", 1)[0]
    initials = "".join(
        part[0].upper()
        for part in ((client.first_name, client.last_name) if client else (display_name, None))
        if part
    )[:2]

    filing_record = None
    if client:
        filing_record = (
            db.query(TaxFiling)
            .filter(TaxFiling.client_id == client.id, TaxFiling.tax_year == selected_year)
            .order_by(TaxFiling.created_at.desc())
            .first()
        )

    filing_data = None
    if filing_record:
        from app.api.dashboard import _build_section_statuses, _STATUS_LABEL
        section_statuses = _build_section_statuses(filing_record)
        completed_sections = sum(1 for s in section_statuses if s["status"] == "complete")
        total_applicable = sum(1 for s in section_statuses if s["status"] != "not_applicable")
        filing_created = f"{filing_record.created_at:%B} {filing_record.created_at.day}, {filing_record.created_at:%Y}"
        
        # Load attached documents
        docs = (
            db.query(FilingDocument)
            .filter(FilingDocument.filing_id == filing_record.id)
            .all()
        )

        filing_data = {
            "id": filing_record.id,
            "case_id": filing_record.get_case_id(db),
            "tax_year": filing_record.tax_year,
            "filing_type": filing_record.filing_type,
            "first_name": filing_record.first_name,
            "last_name": filing_record.last_name,
            "email": filing_record.email,
            "phone": filing_record.phone,
            "date_of_birth": filing_record.date_of_birth.strftime("%B %d, %Y") if filing_record.date_of_birth else None,
            "country_of_citizenship": filing_record.country_of_citizenship,
            "current_country_of_residence": filing_record.current_country_of_residence,
            "us_tax_residency_status": filing_record.us_tax_residency_status,
            "filing_status": filing_record.filing_status,
            "address_line_1": filing_record.address_line_1,
            "address_line_2": filing_record.address_line_2,
            "city": filing_record.city,
            "state_province": filing_record.state_province,
            "postal_code": filing_record.postal_code,
            "country": filing_record.country,
            "spouse_first_name": filing_record.spouse_first_name,
            "spouse_last_name": filing_record.spouse_last_name,
            "spouse_email": filing_record.spouse_email,
            "spouse_phone": filing_record.spouse_phone,
            "spouse_date_of_birth": filing_record.spouse_date_of_birth.strftime("%B %d, %Y") if filing_record.spouse_date_of_birth else None,
            "spouse_country_of_citizenship": filing_record.spouse_country_of_citizenship,
            "spouse_us_tax_residency_status": filing_record.spouse_us_tax_residency_status,
            "income_categories": filing_record.income_categories or [],
            "deductions_credits": filing_record.deductions_credits or [],
            "special_situations": filing_record.special_situations or [],
            "has_previous_return_copy": filing_record.has_previous_return_copy,
            "received_irs_notice": filing_record.received_irs_notice,
            "unresolved_tax_issues": filing_record.unresolved_tax_issues,
            "is_amended_return": filing_record.is_amended_return,
            "status": _STATUS_LABEL.get(filing_record.status, filing_record.status),
            "created_date": filing_created,
            "progress_pct": int((completed_sections / total_applicable) * 100) if total_applicable else 0,
            "documents": docs,
        }

    return templates.TemplateResponse(
        "client/filings.html",
        {
            "request": request,
            "app_name": settings.APP_NAME,
            "tax_year": selected_year,
            "filing": filing_data,
            "display_name": display_name,
            "initials": initials,
        },
    )


@router.get("/filings/new", response_class=HTMLResponse, name="new_filing_page")
async def new_filing_page(
    request: Request,
    account: ClientAccount,
    db: Session = Depends(get_db),
):
    return templates.TemplateResponse(
        "client/new_filing.html",
        {
            "request": request,
            "app_name": settings.APP_NAME,
            "available_years": [2024, 2025, 2026],
        },
    )


@router.post("/filings/new", name="submit_new_filing")
async def submit_new_filing(
    request: Request,
    account: ClientAccount,
    db: Session = Depends(get_db),
):
    # Look up the Client record to get the client_id
    client = db.query(Client).filter(Client.account_id == account.id).first()
    if not client:
        return RedirectResponse(
            url=request.url_for("client_dashboard"),
            status_code=status.HTTP_303_SEE_OTHER,
        )

    form_data = await request.form()

    # Parse checkbox lists (multiple values with the same key)
    income_categories = list(form_data.getlist("income_categories"))
    deductions_credits = list(form_data.getlist("deductions_credits"))
    special_situations = list(form_data.getlist("special_situations"))

    # Build a flat dict of scalar fields, converting empty strings to None
    data: dict = {}
    skip_keys = {"income_categories", "deductions_credits", "special_situations"}
    for key, value in form_data.items():
        if key not in skip_keys:
            data[key] = value if value != "" else None

    # Inject parsed list fields
    data["income_categories"] = income_categories
    data["deductions_credits"] = deductions_credits
    data["special_situations"] = special_situations
    data["dependents"] = []  # Dependents are not yet dynamic in the JS; defaults to empty

    # Cast numeric fields before Pydantic validation
    if data.get("tax_year"):
        data["tax_year"] = int(data["tax_year"])
    if data.get("previous_tax_year_filed"):
        data["previous_tax_year_filed"] = int(data["previous_tax_year_filed"])

    # Validate and parse via Pydantic
    parsed = TaxFilingCreate(**data)

    # Persist to DB
    count = db.query(TaxFiling).count()
    case_num = f"FLI_{count + 1:07d}"
    tax_filing = TaxFiling(client_id=client.id, case_number=case_num, **parsed.model_dump())
    db.add(tax_filing)

    # Update Client profile details with information from the filing form
    # If first filing (or new values provided), keep profile updated
    if parsed.first_name:
        client.first_name = parsed.first_name
    if parsed.last_name:
        client.last_name = parsed.last_name
    if parsed.phone:
        client.phone = parsed.phone
    if parsed.date_of_birth:
        client.date_of_birth = parsed.date_of_birth
    if parsed.address_line_1:
        client.address_line_1 = parsed.address_line_1
    if parsed.address_line_2:
        client.address_line_2 = parsed.address_line_2
    if parsed.city:
        client.city = parsed.city
    if parsed.state_province:
        client.state_province = parsed.state_province
    if parsed.postal_code:
        client.postal_code = parsed.postal_code
    if parsed.country or parsed.current_country_of_residence:
        client.country = parsed.country or parsed.current_country_of_residence

    db.commit()
    db.refresh(tax_filing)

    # Process and save any uploaded document files submitted with the filing form
    category_file_keys = [
        ("Personal", "files_Personal"),
        ("Income", "files_Income"),
        ("Other", "files_Other"),
        ("Deductions", "files_Deductions"),
        ("Other", "files_Other_Extra"),
    ]

    for category, form_key in category_file_keys:
        uploaded_files = form_data.getlist(form_key)
        for upload_file in uploaded_files:
            if hasattr(upload_file, "filename") and upload_file.filename:
                ext = f".{upload_file.filename.split('.')[-1].lower()}" if "." in upload_file.filename else ""
                if ext in settings.ALLOWED_EXTENSIONS:
                    group_id = generate_uuid()
                    stored_name, relative_path, mime_type, file_size = save_document_file(
                        file=upload_file,
                        client_id=client.id,
                        tax_year=tax_filing.tax_year,
                        filing_id=tax_filing.id,
                        category=category,
                        version=1,
                        document_name=upload_file.filename,
                    )
                    doc_record = FilingDocument(
                        filing_id=tax_filing.id,
                        document_group_id=group_id,
                        category=category,
                        document_name=upload_file.filename,
                        original_filename=upload_file.filename,
                        stored_filename=stored_name,
                        file_path=relative_path,
                        file_size=file_size,
                        mime_type=mime_type,
                        version=1,
                        is_latest=True,
                        status="pending_review",
                        uploaded_by_type="client",
                        uploaded_by_id=account.id,
                    )
                    db.add(doc_record)

    db.commit()

    return RedirectResponse(
        url=request.url_for("client_dashboard"),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/documents", response_class=HTMLResponse, name="client_documents_page")
async def client_documents_page(
    request: Request,
    account: ClientAccount,
    db: Session = Depends(get_db),
    tax_year: str | None = Cookie(None),
):
    from app.api.dashboard import _available_tax_years
    available_years = _available_tax_years()

    if not tax_year or not tax_year.isdigit() or int(tax_year) not in available_years:
        selected_year = available_years[0]
    else:
        selected_year = int(tax_year)

    client = db.query(Client).filter(Client.account_id == account.id).first()
    documents_by_category: dict[str, list[FilingDocument]] = {}
    current_filing_id: str | None = None
    has_active_filing: bool = False

    if client:
        # Check if client has any filing for the selected year
        filing = (
            db.query(TaxFiling)
            .filter(TaxFiling.client_id == client.id, TaxFiling.tax_year == selected_year)
            .order_by(TaxFiling.created_at.desc())
            .first()
        )
        # Check overall active filing across any year for sidebar nav visibility
        active_filing_check = (
            db.query(TaxFiling)
            .filter(TaxFiling.client_id == client.id)
            .first()
        )
        if active_filing_check:
            has_active_filing = True

        if filing:
            current_filing_id = filing.id
            docs = (
                db.query(FilingDocument)
                .filter(FilingDocument.filing_id == filing.id, FilingDocument.is_latest == True)
                .order_by(FilingDocument.uploaded_at.desc())
                .all()
            )
            for doc in docs:
                cat = doc.category if doc.category in ["Personal", "Income", "Employment", "Investments", "Deductions", "Credits", "Foreign_Information", "Dependents", "Other"] else "Other"
                if cat not in documents_by_category:
                    documents_by_category[cat] = []
                documents_by_category[cat].append(doc)

    return templates.TemplateResponse(
        "client/documents.html",
        {
            "request": request,
            "app_name": settings.APP_NAME,
            "available_years": available_years,
            "selected_year": selected_year,
            "documents_by_category": documents_by_category,
            "current_filing_id": current_filing_id,
            "has_active_filing": has_active_filing,
        },
    )


@router.post("/filings/{filing_id}/upload", name="upload_client_document")
async def upload_client_document(
    filing_id: str,
    account: ClientAccount,
    category: str = Form(...),
    document_name: Optional[str] = Form(None),
    document_group_id: Optional[str] = Form(None),
    files: List[UploadFile] = File(...),
    db: Session = Depends(get_db),
):
    client = db.query(Client).filter(Client.account_id == account.id).first()
    if not client:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Client profile not found.")

    filing = db.query(TaxFiling).filter(TaxFiling.id == filing_id, TaxFiling.client_id == client.id).first()
    if not filing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tax filing not found.")

    uploaded_records = []

    for file in files:
        if not file.filename:
            continue

        # Extension check
        ext = f".{file.filename.split('.')[-1].lower()}" if "." in file.filename else ""
        if ext not in settings.ALLOWED_EXTENSIONS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"File extension {ext} not allowed. Supported: {', '.join(settings.ALLOWED_EXTENSIONS)}",
            )

        # Version handling
        group_id = document_group_id or generate_uuid()
        current_version = 1

        if document_group_id:
            existing_docs = db.query(FilingDocument).filter(
                FilingDocument.filing_id == filing.id,
                FilingDocument.document_group_id == document_group_id,
            ).all()
            if existing_docs:
                current_version = max(d.version for d in existing_docs) + 1
                for d in existing_docs:
                    d.is_latest = False
                    d.status = "superseded"

        stored_name, relative_path, mime_type, file_size = save_document_file(
            file=file,
            client_id=client.id,
            tax_year=filing.tax_year,
            filing_id=filing.id,
            category=category,
            version=current_version,
            document_name=document_name or file.filename,
        )

        doc_record = FilingDocument(
            filing_id=filing.id,
            document_group_id=group_id,
            category=category,
            document_name=document_name or file.filename,
            original_filename=file.filename,
            stored_filename=stored_name,
            file_path=relative_path,
            file_size=file_size,
            mime_type=mime_type,
            version=current_version,
            is_latest=True,
            status="pending_review",
            uploaded_by_type="client",
            uploaded_by_id=account.id,
        )
        db.add(doc_record)
        uploaded_records.append(doc_record)

    db.commit()

    return JSONResponse(
        status_code=status.HTTP_201_CREATED,
        content={
            "message": f"Successfully uploaded {len(uploaded_records)} file(s).",
            "documents": [
                {
                    "id": doc.id,
                    "document_group_id": doc.document_group_id,
                    "document_name": doc.document_name,
                    "original_filename": doc.original_filename,
                    "category": doc.category,
                    "version": doc.version,
                    "status": doc.status,
                    "file_path": doc.file_path,
                }
                for doc in uploaded_records
            ],
        },
    )


@router.get("/filings/{filing_id}/documents", name="get_client_documents")
async def get_client_documents(
    filing_id: str,
    account: ClientAccount,
    db: Session = Depends(get_db),
):
    client = db.query(Client).filter(Client.account_id == account.id).first()
    if not client:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Client profile not found.")

    filing = db.query(TaxFiling).filter(TaxFiling.id == filing_id, TaxFiling.client_id == client.id).first()
    if not filing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tax filing not found.")

    docs = db.query(FilingDocument).filter(FilingDocument.filing_id == filing.id).order_by(FilingDocument.uploaded_at.desc()).all()

    return JSONResponse(
        content={
            "documents": [
                {
                    "id": doc.id,
                    "document_group_id": doc.document_group_id,
                    "category": doc.category,
                    "document_name": doc.document_name,
                    "original_filename": doc.original_filename,
                    "version": doc.version,
                    "is_latest": doc.is_latest,
                    "status": doc.status,
                    "rejection_reason": doc.rejection_reason,
                    "file_size": doc.file_size,
                    "uploaded_at": doc.uploaded_at.isoformat() if doc.uploaded_at else None,
                }
                for doc in docs
            ]
        }
    )


@router.get("/filings/{filing_id}/documents/{doc_id}/download", name="download_client_document")
async def download_client_document(
    filing_id: str,
    doc_id: str,
    account: ClientAccount,
    db: Session = Depends(get_db),
):
    client = db.query(Client).filter(Client.account_id == account.id).first()
    if not client:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Client profile not found.")

    doc = db.query(FilingDocument).filter(FilingDocument.id == doc_id, FilingDocument.filing_id == filing_id).first()
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found.")

    return FileResponse(path=doc.file_path, filename=doc.original_filename, media_type=doc.mime_type)


def _render_client_section(template_name: str, request: Request, account: ClientAccount, db: Session, tax_year: str | None, active_nav: str):
    from app.api.dashboard import _available_tax_years
    available_years = _available_tax_years()
    if not tax_year or not tax_year.isdigit() or int(tax_year) not in available_years:
        selected_year = available_years[0]
    else:
        selected_year = int(tax_year)

    client = db.query(Client).filter(Client.account_id == account.id).first()
    has_active_filing = False
    if client:
        has_active_filing = db.query(TaxFiling).filter(TaxFiling.client_id == client.id).first() is not None

    return templates.TemplateResponse(
        template_name,
        {
            "request": request,
            "app_name": settings.APP_NAME,
            "available_years": available_years,
            "selected_year": selected_year,
            "has_active_filing": has_active_filing,
            "active_nav": active_nav,
        },
    )


@router.get("/tax-notes", response_class=HTMLResponse, name="client_tax_notes")
async def client_tax_notes(request: Request, account: ClientAccount, db: Session = Depends(get_db), tax_year: str | None = Cookie(None)):
    return _render_client_section("client/tax_notes.html", request, account, db, tax_year, "tax_notes")


@router.get("/tax-summary", response_class=HTMLResponse, name="client_tax_summary")
async def client_tax_summary(request: Request, account: ClientAccount, db: Session = Depends(get_db), tax_year: str | None = Cookie(None)):
    return _render_client_section("client/tax_summary.html", request, account, db, tax_year, "tax_summary")


@router.get("/bank-details", response_class=HTMLResponse, name="client_bank_details")
async def client_bank_details(request: Request, account: ClientAccount, db: Session = Depends(get_db), tax_year: str | None = Cookie(None)):
    return _render_client_section("client/bank_details.html", request, account, db, tax_year, "bank_details")


@router.get("/review-documents", response_class=HTMLResponse, name="client_review_documents")
async def client_review_documents(request: Request, account: ClientAccount, db: Session = Depends(get_db), tax_year: str | None = Cookie(None)):
    return _render_client_section("client/review_documents.html", request, account, db, tax_year, "review_documents")


@router.get("/efile-authorization", response_class=HTMLResponse, name="client_efile_authorization")
async def client_efile_authorization(request: Request, account: ClientAccount, db: Session = Depends(get_db), tax_year: str | None = Cookie(None)):
    return _render_client_section("client/efile_authorization.html", request, account, db, tax_year, "efile_authorization")


@router.get("/final-documents", response_class=HTMLResponse, name="client_final_documents")
async def client_final_documents(request: Request, account: ClientAccount, db: Session = Depends(get_db), tax_year: str | None = Cookie(None)):
    return _render_client_section("client/final_documents.html", request, account, db, tax_year, "final_documents")


@router.get("/update-stage", response_class=HTMLResponse, name="client_update_stage")
async def client_update_stage(request: Request, account: ClientAccount, db: Session = Depends(get_db), tax_year: str | None = Cookie(None)):
    return _render_client_section("client/update_stage.html", request, account, db, tax_year, "update_stage")


@router.get("/update-contact", response_class=HTMLResponse, name="client_update_contact")
async def client_update_contact(request: Request, account: ClientAccount, db: Session = Depends(get_db), tax_year: str | None = Cookie(None)):
    return _render_client_section("client/update_contact.html", request, account, db, tax_year, "update_contact")


@router.get("/refund-request", response_class=HTMLResponse, name="client_refund_request")
async def client_refund_request(request: Request, account: ClientAccount, db: Session = Depends(get_db), tax_year: str | None = Cookie(None)):
    return _render_client_section("client/refund_request.html", request, account, db, tax_year, "refund_request")


@router.get("/fbar", response_class=HTMLResponse, name="client_fbar")
async def client_fbar(request: Request, account: ClientAccount, db: Session = Depends(get_db), tax_year: str | None = Cookie(None)):
    return _render_client_section("client/fbar.html", request, account, db, tax_year, "fbar")


@router.get("/referrals", response_class=HTMLResponse, name="client_referrals")
async def client_referrals(request: Request, account: ClientAccount, db: Session = Depends(get_db), tax_year: str | None = Cookie(None)):
    return _render_client_section("client/referrals.html", request, account, db, tax_year, "referrals")


@router.get("/summary-records", response_class=HTMLResponse, name="client_summary_records")
async def client_summary_records(request: Request, account: ClientAccount, db: Session = Depends(get_db), tax_year: str | None = Cookie(None)):
    return _render_client_section("client/summary_records.html", request, account, db, tax_year, "summary_records")


