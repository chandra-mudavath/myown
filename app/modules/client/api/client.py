import re
from datetime import datetime, timezone
from typing import List, Optional
from urllib.parse import urlencode
from fastapi import APIRouter, Cookie, Depends, Form, HTTPException, File, Request, UploadFile, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.modules.client.api.dashboard import client_shell_context, resolve_tax_year
from app.core.config import settings
from app.core.database import get_db
from app.core.files import send_file
from app.core.deps import ClientAccount
from app.core.templates import templates
from app.modules.client.models.client import Client
from app.platform.models.filing_document import FilingDocument
from app.platform.models.lookups import CaseStage, ChatQueryTopic
from app.platform.models.tax_filing import TaxFiling
from app.modules.client.schemas.tax_filing import TaxFilingCreate
from app.platform.services import chat_service, intake, payments, paypal, referrals
from app.platform.models.auth import AuthAccount
from app.platform.services.catalog import case_document_types, document_categories, open_tax_years, tax_types
from app.platform.services.document_review import (REVIEW_CATEGORY, account_names, add_comment, comment_threads, review_documents,
                                                   store_upload)
from app.platform.services.storage_service import generate_uuid, save_document_file

router = APIRouter(prefix="/client", tags=["client"])


@router.get("/filings", response_class=HTMLResponse, name="my_filings_page")
async def my_filings_page(
    request: Request,
    account: ClientAccount,
    db: Session = Depends(get_db),
    tax_year: str | None = Cookie(None),
):
    selected_year = resolve_tax_year(db, tax_year)

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
        from app.modules.client.api.dashboard import _build_section_statuses, _STATUS_LABEL
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
        request, "client/filings.html",
        {
            "request": request,
            **client_shell_context(db, account, tax_year),
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
    tax_year: str | None = Cookie(None),
):
    # No longer used (client feedback, Oct 2026): clients fill Personal Info, Tax Notes, Upload Documents and
    # Bank Details instead, and the year's case is created on their first save. Old links land on Personal Info.
    # The form below (client/new_filing.html) is kept, switched off.
    if True:
        return RedirectResponse("/client/personal-info", status_code=status.HTTP_303_SEE_OTHER)
    shell = client_shell_context(db, account, tax_year)
    return templates.TemplateResponse(
        request, "client/new_filing.html",
        {
            "request": request,
            **shell,
            "filing_years": open_tax_years(db),
            "tax_types": tax_types(db),
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
    tax_filing = TaxFiling(client_id=client.id, **parsed.model_dump())  # case_number filled in on insert
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
    from app.modules.client.api.dashboard import _available_tax_years
    available_years = _available_tax_years(db)
    category_labels = document_categories(db)

    if not tax_year or not tax_year.isdigit() or int(tax_year) not in available_years:
        selected_year = available_years[0]
    else:
        selected_year = int(tax_year)

    client = db.query(Client).filter(Client.account_id == account.id).first()
    documents_by_category: dict[str, list[FilingDocument]] = {}
    current_filing_id: str | None = None
    has_active_filing: bool = False
    uploaders: dict[str, str] = {}
    threads: dict = {}
    filing = None

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
                .filter(FilingDocument.filing_id == filing.id, FilingDocument.is_latest == True,
                        FilingDocument.category != REVIEW_CATEGORY)  # those are on Review Documents
                .order_by(FilingDocument.uploaded_at.desc())
                .all()
            )
            for doc in docs:
                cat = doc.category if doc.category in category_labels else "Other"
                if cat not in documents_by_category:
                    documents_by_category[cat] = []
                documents_by_category[cat].append(doc)

            # "Me" for the client's own uploads, otherwise the tax team member's name
            names = account_names(db, {d.uploaded_by_id for d in docs})
            for d in docs:
                uploaders[d.id] = "Me" if d.uploaded_by_id == account.id else names.get(d.uploaded_by_id, {}).get("name", "Tax team")
            threads = comment_threads(db, filing.id)

    return templates.TemplateResponse(
        request, "client/documents.html",
        {
            "request": request,
            **client_shell_context(db, account, tax_year),
            "available_years": available_years,
            "selected_year": selected_year,
            "documents_by_category": documents_by_category,
            "category_labels": category_labels,
            "document_types": case_document_types(db),
            "checklist": intake.checklist(db, filing) if client and filing else None,
            "upload_saved": request.query_params.get("saved", ""),
            "current_filing_id": current_filing_id,
            "has_active_filing": has_active_filing,
            "uploaders": uploaders,
            "threads": threads,
            "account_id": account.id,
        },
    )


@router.post("/filings/{filing_id}/documents/{doc_id}/reply", name="client_reply_document")
async def client_reply_document(
    filing_id: str,
    doc_id: str,
    account: ClientAccount,
    body: str = Form(""),
    file: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db),
):
    """Client answers a rejected document: a comment, a re-uploaded file, or both."""
    client = db.query(Client).filter(Client.account_id == account.id).first()
    filing = (
        db.query(TaxFiling).filter(TaxFiling.id == filing_id, TaxFiling.client_id == client.id).first() if client else None
    )
    doc = (
        db.query(FilingDocument).filter(FilingDocument.id == doc_id, FilingDocument.filing_id == filing_id).first()
        if filing else None
    )
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found.")

    body = body.strip()
    has_file = file is not None and bool(file.filename)
    if not body and not has_file:
        return RedirectResponse(f"/client/documents?reply_error={doc_id}#doc-{doc.document_group_id}", status_code=status.HTTP_303_SEE_OTHER)

    target = doc
    if has_file:
        target = store_upload(
            db,
            client=client,
            filing=filing,
            file=file,
            category=doc.category,
            document_name=doc.document_name,
            document_group_id=doc.document_group_id,
            uploaded_by_type="client",
            uploaded_by_id=account.id,
        )
        db.flush()
    if body or has_file:
        add_comment(
            db,
            document=target,
            kind="reupload" if has_file else "reply",
            body=body or f"Uploaded a new version: {file.filename}",
            author_type="client",
            author_account_id=account.id,
        )
    db.commit()
    return RedirectResponse(f"/client/documents#doc-{doc.document_group_id}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/documents/upload", name="upload_client_document_for_year")
async def upload_client_document_for_year(
    account: ClientAccount,
    category: str = Form(...),
    document_name: Optional[str] = Form(None),
    document_type: Optional[str] = Form(None),
    files: List[UploadFile] = File(...),
    db: Session = Depends(get_db),
    tax_year: str | None = Cookie(None),
):
    """Upload for the selected tax year. The year's case is created on the first upload if needed
    (client feedback, Oct 2026: no separate New filing step)."""
    client = _own_client(db, account)
    year = resolve_tax_year(db, tax_year)
    filing = intake.ensure_filing(db, client, db.get(AuthAccount, account.id), year)
    codes = {t.code for t in case_document_types(db)}
    saved = []
    for file in files:
        if not file.filename:
            continue
        saved.append(store_upload(db, client=client, filing=filing, file=file, category=category, document_name=document_name,
                                  document_group_id=None, uploaded_by_type="client", uploaded_by_id=account.id,
                                  document_type=document_type if document_type in codes else None))
    db.commit()
    return JSONResponse(status_code=status.HTTP_201_CREATED,
                        content={"message": f"Successfully uploaded {len(saved)} file(s).", "filing_id": filing.id})


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
        doc_record = store_upload(
            db,
            client=client,
            filing=filing,
            file=file,
            category=category,
            document_name=document_name,
            document_group_id=document_group_id,
            uploaded_by_type="client",
            uploaded_by_id=account.id,
        )
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
    inline: bool = False,
    db: Session = Depends(get_db),
):
    client = db.query(Client).filter(Client.account_id == account.id).first()
    if not client:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Client profile not found.")

    # Only serve documents from the signed-in client's own filings
    doc = (
        db.query(FilingDocument)
        .join(TaxFiling, TaxFiling.id == FilingDocument.filing_id)
        .filter(FilingDocument.id == doc_id, FilingDocument.filing_id == filing_id, TaxFiling.client_id == client.id)
        .first()
    )
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found.")

    return send_file(doc.file_path, doc.original_filename, doc.mime_type, inline=inline)  # inline: preview on Review Documents


def _render_client_section(template_name: str, request: Request, account: ClientAccount, db: Session, tax_year: str | None, active_nav: str,
                           extra=None):
    shell = client_shell_context(db, account, tax_year)
    client = shell["client"]
    has_active_filing = False
    filing = None
    if client:
        has_active_filing = db.query(TaxFiling).filter(TaxFiling.client_id == client.id).first() is not None
        filing = (
            db.query(TaxFiling)
            .filter(TaxFiling.client_id == client.id, TaxFiling.tax_year == shell["selected_year"])
            .order_by(TaxFiling.created_at.desc())
            .first()
        )

    return templates.TemplateResponse(
        request, template_name,
        {
            "request": request,
            **shell,
            "has_active_filing": has_active_filing,
            "filing": filing,
            # The filing's workflow stage (name + the status the client sees), from case_stages
            "stage": db.query(CaseStage).filter(CaseStage.code == filing.status).first() if filing else None,
            "active_nav": active_nav,
            **(extra(client, filing) if extra and client else {}),
        },
    )


@router.get("/tax-notes", response_class=HTMLResponse, name="client_tax_notes")
async def client_tax_notes(request: Request, account: ClientAccount, db: Session = Depends(get_db), tax_year: str | None = Cookie(None)):
    def extra(client, filing):
        row = intake.section(db, filing, intake.TAX_NOTES)
        return {"notes": (row.data if row else {}) or {}, "notes_row": row, "notes_saved": request.query_params.get("saved", "")}
    return _render_client_section("client/tax_notes.html", request, account, db, tax_year, "tax_notes", extra=extra)


@router.post("/tax-notes", name="client_save_tax_notes")
async def client_save_tax_notes(request: Request, account: ClientAccount, db: Session = Depends(get_db), tax_year: str | None = Cookie(None)):
    """Save one Tax Notes sheet (income or expenses). The first save creates the year's case."""
    form = await request.form()
    sheet = form.get("sheet")
    client = _own_client(db, account)
    filing = intake.ensure_filing(db, client, db.get(AuthAccount, account.id), resolve_tax_year(db, tax_year))
    data = intake.form_to_dict(form, skip={"sheet", "confirm"})
    intake.save_section(db, filing, intake.TAX_NOTES, data, account.id, merge=True, submitted=sheet == "expenses")
    if sheet == "expenses":
        # Both sheets done: show the documents their answers ask for
        return RedirectResponse("/client/documents?saved=notes#checklist", status_code=status.HTTP_303_SEE_OTHER)
    return RedirectResponse("/client/tax-notes?saved=income#expenses", status_code=status.HTTP_303_SEE_OTHER)


def _billing_extra(request: Request, db: Session):
    def billing(client, filing):
        return {**payments.client_billing(db, client.id, filing), "pay_methods": payments.CLIENT_METHODS,
                "currencies": payments.CURRENCIES, "paypal_on": paypal.enabled(), "upi_id": settings.PAYMENT_UPI_ID, "upi_payee": settings.PAYMENT_UPI_PAYEE,
                "bank_lines": [line for line in settings.PAYMENT_BANK_DETAILS.splitlines() if line.strip()],
                "pay_saved": request.query_params.get("saved", ""), "pay_error": request.query_params.get("error", "")}
    return billing


@router.get("/tax-summary", response_class=HTMLResponse, name="client_tax_summary")
async def client_tax_summary(request: Request, account: ClientAccount, db: Session = Depends(get_db), tax_year: str | None = Cookie(None)):
    return _render_client_section("client/tax_summary.html", request, account, db, tax_year, "tax_summary", extra=_billing_extra(request, db))


@router.get("/make-payment", response_class=HTMLResponse, name="client_make_payment")
async def client_make_payment(request: Request, account: ClientAccount, db: Session = Depends(get_db), tax_year: str | None = Cookie(None)):
    """Make Payment as its own page, separate from Tax Summary (client feedback, Oct 2026)."""
    return _render_client_section("client/make_payment.html", request, account, db, tax_year, "make_payment", extra=_billing_extra(request, db))


@router.post("/payments/manual", name="client_payment_manual")
async def client_payment_manual(
    account: ClientAccount,
    db: Session = Depends(get_db),
    invoice_id: str = Form(""),
    payment_method: str = Form(""),
    transaction_reference: str = Form(""),
    sent_amount: str = Form(""),
    sent_currency: str = Form(""),
    pay_amount: str = Form(""),
    proof: UploadFile | None = File(None),
):
    """The client paid by UPI or bank transfer and sends the reference for us to verify."""
    client = _own_client(db, account)
    err = payments.submit_manual(db, account.id, client.id, invoice_id, payment_method, transaction_reference,
                                 sent_amount, sent_currency, proof, pay_amount)
    return _back_to_payments(err, "Thanks! We've received your payment details and will confirm once verified.")


def _back_to_payments(err: str | None, saved: str) -> RedirectResponse:
    query = urlencode({"error": err} if err else {"saved": saved})
    # Errors go back to the payment form; a saved payment shows in the history on Tax Summary.
    # Was: f"/client/tax-summary?{query}#{'payment' if err else 'payment-details'}" (before Make Payment had its own page)
    if err:
        return RedirectResponse(f"/client/make-payment?{query}", status_code=303)
    return RedirectResponse(f"/client/tax-summary?{query}#payment-details", status_code=303)


def _own_client(db: Session, account) -> Client:
    client = db.query(Client).filter(Client.account_id == account.id).first()
    if not client:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Client profile not found.")
    return client


@router.post("/payments/paypal/start", name="client_paypal_start")
async def client_paypal_start(request: Request, account: ClientAccount, db: Session = Depends(get_db), invoice_id: str = Form(""),
                             pay_amount: str = Form("")):
    """Create a PayPal order for the invoice and send the client to PayPal to approve it."""
    client = _own_client(db, account)
    base = (settings.PUBLIC_BASE_URL or str(request.base_url)).rstrip("/")
    link, err = payments.start_paypal(db, account.id, client.id, invoice_id,
                                      return_url=f"{base}/client/payments/paypal/return",
                                      cancel_url=f"{base}/client/payments/paypal/cancel", pay_amount=pay_amount)
    if err:
        return _back_to_payments(err, "")
    return RedirectResponse(link, status_code=303)


@router.get("/payments/paypal/return", name="client_paypal_return")
async def client_paypal_return(account: ClientAccount, db: Session = Depends(get_db), token: str = ""):
    """PayPal sends the client back here after they approve; capture the payment now."""
    err = payments.finish_paypal(db, token, _own_client(db, account).id)
    return _back_to_payments(err, "Thank you! Your PayPal payment was received.")


@router.get("/payments/paypal/cancel", name="client_paypal_cancel")
async def client_paypal_cancel(account: ClientAccount, db: Session = Depends(get_db), token: str = ""):
    payments.cancel_paypal(db, token, _own_client(db, account).id)
    return _back_to_payments("PayPal checkout was cancelled. You were not charged.", "")


@router.get("/bank-details", response_class=HTMLResponse, name="client_bank_details")
async def client_bank_details(request: Request, account: ClientAccount, db: Session = Depends(get_db), tax_year: str | None = Cookie(None)):
    def extra(client, filing):
        row = intake.section(db, filing, intake.BANK)
        return {"bank": (row.data if row else {}) or {}, "bank_row": row, "bank_saved": request.query_params.get("saved", ""),
                "bank_error": request.query_params.get("error", "")}
    return _render_client_section("client/bank_details.html", request, account, db, tax_year, "bank_details", extra=extra)


@router.post("/bank-details", name="client_save_bank_details")
async def client_save_bank_details(account: ClientAccount, db: Session = Depends(get_db), tax_year: str | None = Cookie(None),
                                   refund_method: str = Form("direct"), bank_name: str = Form(""), account_type: str = Form(""),
                                   routing_number: str = Form(""), account_number: str = Form(""), holder_name: str = Form("")):
    """Save the refund bank details for the selected year. The first save creates the year's case."""
    method = "check" if refund_method == "check" else "direct"
    data = {"refund_method": method}
    if method == "direct":
        routing, number = re.sub(r"\D", "", routing_number), re.sub(r"\D", "", account_number)
        err = None
        if not bank_name.strip() or account_type not in ("checking", "savings") or not holder_name.strip():
            err = "Fill in the bank name, account type and account holder name."
        elif len(routing) != 9:
            err = "The routing number (electronic) must be 9 digits."
        elif not 4 <= len(number) <= 17:
            err = "The account number must be 4 to 17 digits."
        if err:
            return RedirectResponse(f"/client/bank-details?{urlencode({'error': err})}", status_code=status.HTTP_303_SEE_OTHER)
        data.update(bank_name=bank_name.strip()[:120], account_type=account_type, routing_number=routing, account_number=number,
                    holder_name=holder_name.strip()[:120])
    client = _own_client(db, account)
    filing = intake.ensure_filing(db, client, db.get(AuthAccount, account.id), resolve_tax_year(db, tax_year))
    intake.save_section(db, filing, intake.BANK, data, account.id, submitted=True)
    return RedirectResponse(f"/client/bank-details?{urlencode({'saved': 'Bank details saved.'})}", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/review-documents", response_class=HTMLResponse, name="client_review_documents")
async def client_review_documents(request: Request, account: ClientAccount, db: Session = Depends(get_db), tax_year: str | None = Cookie(None)):
    def billing(client, filing):
        extra = payments.client_billing(db, client.id, filing)
        if filing:
            extra["review_docs"] = review_documents(db, filing.id)
        extra["review_error"] = request.query_params.get("error", "")
        extra["review_saved"] = request.query_params.get("saved", "")
        return extra
    return _render_client_section("client/review_documents.html", request, account, db, tax_year, "review_documents", extra=billing)


@router.post("/review-documents/{doc_id}/decision", name="client_review_decision")
async def client_review_decision(doc_id: str, account: ClientAccount, db: Session = Depends(get_db),
                                 action: str = Form(""), comment: str = Form("")):
    """The client approves a document the tax team prepared, or rejects it with a comment for the team."""
    client = _own_client(db, account)
    doc = (
        db.query(FilingDocument)
        .join(TaxFiling, TaxFiling.id == FilingDocument.filing_id)
        .filter(FilingDocument.id == doc_id, TaxFiling.client_id == client.id, FilingDocument.category == REVIEW_CATEGORY,
                FilingDocument.is_latest.is_(True))
        .first()
    )
    if not doc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found.")
    if action not in ("approve", "reject"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unknown action.")
    comment = comment.strip()
    if action == "reject" and not comment:
        return RedirectResponse(f"/client/review-documents?error={doc_id}#rd-{doc_id}", status_code=status.HTTP_303_SEE_OTHER)
    doc.status = "client_approved" if action == "approve" else "client_rejected"
    doc.rejection_reason = comment[:1000] if action == "reject" else None
    doc.reviewed_at = datetime.now(timezone.utc)
    doc.reviewed_by_id = account.id
    add_comment(db, document=doc, kind="client_rejection" if action == "reject" else "client_approval",
                body=comment or "Approved.", author_type="client", author_account_id=account.id)
    db.commit()
    saved = "approved" if action == "approve" else "rejected"
    return RedirectResponse(f"/client/review-documents?saved={saved}#rd-{doc_id}", status_code=status.HTTP_303_SEE_OTHER)


def _feedback(request: Request) -> dict:
    """?saved= / ?error= from the redirect after a form post."""
    return {"form_saved": request.query_params.get("saved", ""), "form_error": request.query_params.get("error", "")}


def _redirect(url: str, err: str | None, saved: str, anchor: str = "", **extra) -> RedirectResponse:
    query = urlencode({"error": err} if err else {"saved": saved, **extra})
    return RedirectResponse(f"{url}?{query}{anchor}", status_code=status.HTTP_303_SEE_OTHER)


def _year_case(db: Session, account, tax_year: str | None) -> TaxFiling:
    """The selected year's case; created on the first save, like Tax Notes and Bank Details."""
    client = _own_client(db, account)
    return intake.ensure_filing(db, client, db.get(AuthAccount, account.id), resolve_tax_year(db, tax_year))


@router.get("/efile-authorization", response_class=HTMLResponse, name="client_efile_authorization")
async def client_efile_authorization(request: Request, account: ClientAccount, db: Session = Depends(get_db), tax_year: str | None = Cookie(None)):
    def extra(client, filing):
        return {"efile_row": intake.section(db, filing, intake.EFILE), **_feedback(request)}
    return _render_client_section("client/efile_authorization.html", request, account, db, tax_year, "efile_authorization", extra=extra)


@router.post("/efile-authorization", name="client_save_efile_authorization")
async def client_save_efile_authorization(request: Request, account: ClientAccount, db: Session = Depends(get_db),
                                          tax_year: str | None = Cookie(None)):
    """Form 8879: the client's self-select PIN(s) and consent, so the tax team can e-file the return."""
    form = await request.form()
    data, err = intake.efile_from_form(form)
    if err:
        return _redirect("/client/efile-authorization", err, "")
    data["ip_address"] = request.client.host if request.client else ""
    intake.save_section(db, _year_case(db, account, tax_year), intake.EFILE, data, account.id, submitted=True)
    return _redirect("/client/efile-authorization", None, "Thank you. Your e-file authorization was sent to your tax team.")


@router.get("/final-documents", response_class=HTMLResponse, name="client_final_documents")
async def client_final_documents(request: Request, account: ClientAccount, db: Session = Depends(get_db), tax_year: str | None = Cookie(None)):
    return _render_client_section("client/final_documents.html", request, account, db, tax_year, "final_documents")


@router.get("/update-stage", response_class=HTMLResponse, name="client_update_stage")
async def client_update_stage(request: Request, account: ClientAccount, db: Session = Depends(get_db), tax_year: str | None = Cookie(None)):
    def extra(client, filing):
        return {**_feedback(request), "request_thread": request.query_params.get("thread", "")}
    return _render_client_section("client/update_stage.html", request, account, db, tax_year, "update_stage", extra=extra)


STAGE_REQUESTS = {"status_check": "General status check", "expedite": "Expedited review request",
                  "question": "Question about my stage"}


@router.post("/update-stage/request", name="client_stage_request")
async def client_stage_request(account: ClientAccount, db: Session = Depends(get_db), tax_year: str | None = Cookie(None),
                               request_type: str = Form("status_check"), stage_comments: str = Form("")):
    """Request an update: sent to the tax team as a question in Messages, linked to the year's case."""
    if not stage_comments.strip():
        return _redirect("/client/update-stage", "Write a message for your tax team.", "", "#req-title")
    client = _own_client(db, account)
    year = resolve_tax_year(db, tax_year)
    filing = intake.filing_for(db, client, year)
    topics = db.query(ChatQueryTopic).filter(ChatQueryTopic.is_active.is_(True))
    topic = topics.filter(ChatQueryTopic.code == "FILING_STATUS").first() or topics.order_by(ChatQueryTopic.sort_order).first()
    if not topic:
        return _redirect("/client/update-stage", "Messaging isn't available right now. Please try again later.", "", "#req-title")
    viewer = chat_service.viewer_for(db, db.get(AuthAccount, account.id))
    subject = f"{STAGE_REQUESTS.get(request_type, STAGE_REQUESTS['status_check'])} · {year}"
    try:
        thread = chat_service.create_query(db, viewer, topic.id, subject, stage_comments, case_id=filing.id if filing else None)
    except HTTPException as e:
        return _redirect("/client/update-stage", str(e.detail), "", "#req-title")
    return _redirect("/client/update-stage", None, "Request sent to your tax team.", "#req-title", thread=thread.id)


@router.get("/update-contact", response_class=HTMLResponse, name="client_update_contact")
async def client_update_contact(request: Request, account: ClientAccount, db: Session = Depends(get_db), tax_year: str | None = Cookie(None)):
    return _render_client_section("client/update_contact.html", request, account, db, tax_year, "update_contact")


@router.get("/refund-request", response_class=HTMLResponse, name="client_refund_request")
async def client_refund_request(request: Request, account: ClientAccount, db: Session = Depends(get_db), tax_year: str | None = Cookie(None)):
    return _render_client_section("client/refund_request.html", request, account, db, tax_year, "refund_request")


@router.get("/fbar", response_class=HTMLResponse, name="client_fbar")
async def client_fbar(request: Request, account: ClientAccount, db: Session = Depends(get_db), tax_year: str | None = Cookie(None)):
    def extra(client, filing):
        row = intake.section(db, filing, intake.FBAR)
        return {"fbar": intake.fbar_summary(row.data if row else None), "fbar_row": row, **_feedback(request)}
    return _render_client_section("client/fbar.html", request, account, db, tax_year, "fbar", extra=extra)


@router.post("/fbar", name="client_save_fbar")
async def client_save_fbar(request: Request, account: ClientAccount, db: Session = Depends(get_db), tax_year: str | None = Cookie(None)):
    """One FBAR step: Part I filer details, add / remove an account (Parts II, III), or Part IV submit."""
    form = await request.form()
    part = str(form.get("part") or "")
    client = _own_client(db, account)
    filing = intake.filing_for(db, client, resolve_tax_year(db, tax_year))
    row = intake.section(db, filing, intake.FBAR)
    data = dict(row.data) if row else {}
    accounts = list(data.get("accounts") or [])
    back = "/client/fbar"
    panel = {"sep": "#part2", "joint": "#part3"}

    if part == "filer":
        filer, err = intake.fbar_filer_from_form(form, data.get("filer") or {})
        if err:
            return _redirect(back, err, "", "#part1")
        update, saved, anchor = {"filer": filer}, "Part I saved.", "#part2"
    elif part in panel:
        flag = "no_separate" if part == "sep" else "no_joint"
        if form.get(part) == "no":
            update, saved, anchor = {flag: True}, "Saved.", "#part3" if part == "sep" else "#part4"
        else:
            acct, err = intake.fbar_account_from_form(form, part)
            if not err and len(accounts) >= intake.MAX_FBAR_ACCOUNTS:
                err = f"You can add up to {intake.MAX_FBAR_ACCOUNTS} accounts. Tell your tax team about the rest."
            if err:
                return _redirect(back, err, "", panel[part])
            update, saved, anchor = {"accounts": accounts + [acct], flag: False}, "Account saved. Add another, or continue.", panel[part]
    elif part == "remove":
        index = str(form.get("index") or "")
        if not (index.isdigit() and int(index) < len(accounts)):
            return _redirect(back, "That account was already removed.", "", "#part2")
        removed = accounts.pop(int(index))
        update, saved, anchor = {"accounts": accounts}, "Account removed.", panel.get(removed.get("kind"), "#part2")
    elif part == "submit":
        if not data.get("filer"):
            return _redirect(back, "Fill in Part I (filer information) first.", "", "#part1")
        if form.get("confirm") is None:
            return _redirect(back, "Tick the confirmation to submit.", "", "#part4")
        intake.save_section(db, filing, intake.FBAR, {}, account.id, merge=True, submitted=True)
        return _redirect(back, None, "FBAR details submitted to your tax team.", "#part4")
    else:
        return _redirect(back, "Something went wrong. Please try again.", "")

    intake.save_section(db, filing or _year_case(db, account, tax_year), intake.FBAR, update, account.id, merge=True)
    return _redirect(back, None, saved, anchor)


@router.get("/referrals", response_class=HTMLResponse, name="client_referrals")
async def client_referrals(request: Request, account: ClientAccount, db: Session = Depends(get_db), tax_year: str | None = Cookie(None)):
    def extra(client, filing):
        rows = referrals.client_referrals(db, client)
        return {"referral_rows": rows, "ref_code": referrals.referral_code(client), "relationships": referrals.RELATIONSHIPS,
                "ref_saved": request.query_params.get("saved", ""), "ref_error": request.query_params.get("error", ""),
                "ref_counts": {"all": len(rows), "pending": sum(1 for r in rows if r["lead"].status in referrals.OPEN_STATUSES),
                               "joined": sum(1 for r in rows if r["lead"].status in ("signed_up", "filed"))}}
    return _render_client_section("client/referrals.html", request, account, db, tax_year, "referrals", extra=extra)


@router.post("/referrals", name="client_add_referral")
async def client_add_referral(account: ClientAccount, db: Session = Depends(get_db), name: str = Form(""), phone: str = Form(""),
                              email: str = Form(""), relationship: str = Form(""), note: str = Form("")):
    """The client refers a colleague; the tax team follows it up from the staff and admin Referrals pages."""
    client = _own_client(db, account)
    err = referrals.add_lead(db, referrer=client, name=name, phone=phone, email=email, relationship=relationship, note=note,
                             source="form", actor_account_id=account.id)
    query = urlencode({"error": err} if err else {"saved": f"Thanks! We'll get in touch with {' '.join(name.split())}."})
    return RedirectResponse(f"/client/referrals?{query}#refer-form", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/summary-records", response_class=HTMLResponse, name="client_summary_records")
async def client_summary_records(request: Request, account: ClientAccount, db: Session = Depends(get_db), tax_year: str | None = Cookie(None)):
    return _render_client_section("client/summary_records.html", request, account, db, tax_year, "summary_records")


