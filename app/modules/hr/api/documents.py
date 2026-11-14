"""HR workspace: payslips, offer letters and the other letters, all made from fixed templates.

Flow for every document: fill the form → either "Generate & download" (finalised at once and downloaded
as a PDF) or "Save as draft" (marked DRAFT; change and regenerate as often as needed, then Finalise) →
share (email, or the staff member's own portal). Any document can be downloaded as a PDF.
"""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import HrAccount
from app.core.templates import templates
from app.modules.hr.api.common import employee_or_404, hr_ctx, pdf_download, redirect
from app.modules.hr.models.records import HrDocument
from app.modules.hr.services import documents, notify, people, time_off

router = APIRouter(prefix="/hr", tags=["hr"])


def _doc_or_404(db: Session, doc_id: str) -> HrDocument:
    doc = documents.get(db, doc_id)
    if not doc or (doc.staff_id and not people.employee(db, doc.staff_id)):
        raise HTTPException(status_code=404, detail="Document not found")
    return doc


# ── Payslips ──────────────────────────────────────────────────────────────

@router.get("/payslips", response_class=HTMLResponse, name="hr_payslips")
def hr_payslips(request: Request, account: HrAccount, db: Session = Depends(get_db), month: str = "",
                saved: str = "", error: str = ""):
    y, m = time_off.parse_month(month)
    docs = documents.payslips(db, documents.period_of(y, m))
    return templates.TemplateResponse(request, "hr/payslips.html", hr_ctx(
        request, db, account, active_nav="payslips", nav=time_off.month_nav(y, m), docs=docs, money=documents.money,
        drafts=sum(1 for d in docs if d.status == "draft"), saved=saved, error=error))


@router.post("/payslips/generate")
def hr_generate_payslips(account: HrAccount, db: Session = Depends(get_db), month: str = Form("")):
    y, m = time_off.parse_month(month)
    made, skipped = documents.generate_payslips(db, account.id, y, m)
    note = f"{made} draft payslip{'s' if made != 1 else ''} ready to check."
    if skipped:
        note += f" Skipped: {', '.join(skipped)}."
    return redirect(f"/hr/payslips?month={y:04d}-{m:02d}", saved=note)


@router.post("/payslips/finalise")
def hr_finalise_payslips(request: Request, account: HrAccount, db: Session = Depends(get_db), month: str = Form("")):
    y, m = time_off.parse_month(month)
    done = documents.finalise_payslips(db, account.id, documents.period_of(y, m))
    base = notify.base_url(request)
    for doc in done:
        notify.payslip_ready(db, base, doc)
    if done:
        notify.payslips_finalised(db, base, date(y, m, 1).strftime("%B %Y"), len(done), done[0].data.get("pay_date", ""))
    return redirect(f"/hr/payslips?month={y:04d}-{m:02d}",
                    saved=f"{len(done)} payslip{'s' if len(done) != 1 else ''} finalised. Staff can now see them." if done else None,
                    error=None if done else "No draft payslips to finalise.")


@router.get("/payslips/download")
def hr_download_payslips(account: HrAccount, db: Session = Depends(get_db), month: str = ""):
    """Every final payslip of the month, one PDF each, in a .zip."""
    y, m = time_off.parse_month(month)
    docs = [d for d in documents.payslips(db, documents.period_of(y, m)) if d.status == "final"]
    if not docs:
        return redirect(f"/hr/payslips?month={y:04d}-{m:02d}", error="No final payslips to download for this month.")
    return pdf_download(db, docs, f"/hr/payslips?month={y:04d}-{m:02d}", zip_name=f"Payslips_{y:04d}-{m:02d}.zip")


# ── Offers ────────────────────────────────────────────────────────────────

@router.get("/offers", response_class=HTMLResponse, name="hr_offers")
def hr_offers(request: Request, account: HrAccount, db: Session = Depends(get_db), status: str = "", saved: str = "", error: str = ""):
    return templates.TemplateResponse(request, "hr/offers.html", hr_ctx(
        request, db, account, active_nav="offers", offers=documents.offers(db, status), status=status,
        offer_statuses=documents.OFFER_STATUSES, money=documents.money, saved=saved, error=error))


# ── New document (offer or letter) ────────────────────────────────────────

@router.get("/documents/new", response_class=HTMLResponse, name="hr_new_document")
def hr_new_document(request: Request, account: HrAccount, db: Session = Depends(get_db), kind: str = "OFFER", staff_id: str = ""):
    k = documents.KINDS.get(kind)
    if not k:
        raise HTTPException(status_code=404, detail="Document not found")
    staff = employee_or_404(db, staff_id) if not k.for_candidate else None
    return templates.TemplateResponse(request, "hr/document_form.html", hr_ctx(
        request, db, account, active_nav="offers" if k.for_candidate else "employees", kind=k, staff=staff,
        values=documents.defaults(db, k, staff), people=people, doc=None, error=""))


@router.post("/documents", response_class=HTMLResponse)
async def hr_create_document(request: Request, account: HrAccount, db: Session = Depends(get_db)):
    form = {k: str(v) for k, v in (await request.form()).items()}
    k = documents.KINDS.get(form.get("kind", ""))
    if not k:
        raise HTTPException(status_code=404, detail="Document not found")
    staff = employee_or_404(db, form.get("staff_id", "")) if not k.for_candidate else None
    doc, err = documents.create(db, account.id, k.code, staff, form)
    if err:
        return templates.TemplateResponse(request, "hr/document_form.html", hr_ctx(
            request, db, account, active_nav="offers" if k.for_candidate else "employees", kind=k, staff=staff,
            values=form, people=people, doc=None, error=err), status_code=400)
    if form.get("action") == "download":  # Generate & download: final straight away, then the PDF
        _finalise(request, db, account, doc)
        return redirect(f"/hr/documents/{doc.id}?download=1", saved=_finalised_note(doc))
    return redirect(f"/hr/documents/{doc.id}", saved="Draft ready. Check it, change anything, then finalise.")


# ── One document ──────────────────────────────────────────────────────────

@router.get("/documents/{doc_id}", response_class=HTMLResponse, name="hr_document")
def hr_document(request: Request, doc_id: str, account: HrAccount, db: Session = Depends(get_db), saved: str = "", error: str = "",
                download: str = ""):
    doc = _doc_or_404(db, doc_id)
    kind = documents.KINDS.get(doc.kind)
    return templates.TemplateResponse(request, "hr/document.html", hr_ctx(
        request, db, account, active_nav="payslips" if doc.kind == "PAYSLIP" else ("offers" if doc.kind == "OFFER" else "employees"),
        doc=doc, kind=kind, letter=documents.render(db, doc), title=documents.title(doc), values=doc.data,
        extras=documents.PAYSLIP_EXTRAS, offer_statuses=documents.OFFER_STATUSES, people=people, saved=saved, error=error,
        download=bool(download) and doc.status != "void"))


@router.post("/documents/{doc_id}")
async def hr_update_document(request: Request, doc_id: str, account: HrAccount, db: Session = Depends(get_db)):
    doc = _doc_or_404(db, doc_id)
    form = {k: str(v) for k, v in (await request.form()).items()}
    err = documents.update(db, account.id, doc, form)
    return redirect(f"/hr/documents/{doc_id}", error=err, saved=None if err else "Draft updated.")


def _finalise(request: Request, db: Session, account, doc: HrDocument) -> str | None:
    err = documents.finalise(db, account.id, doc)
    if not err:
        base = notify.base_url(request)
        if doc.kind == "PAYSLIP":
            notify.payslip_ready(db, base, doc)
        else:
            notify.letter_finalised(db, base, doc)
    return err


def _finalised_note(doc: HrDocument) -> str:
    return "Finalised. " + ("Use Send offer to email it to the candidate." if doc.kind == "OFFER" else "The employee can now see it in their portal.")


@router.post("/documents/{doc_id}/finalise")
def hr_finalise_document(request: Request, doc_id: str, account: HrAccount, db: Session = Depends(get_db)):
    doc = _doc_or_404(db, doc_id)
    err = _finalise(request, db, account, doc)
    return redirect(f"/hr/documents/{doc_id}", error=err, saved=None if err else _finalised_note(doc))


@router.post("/documents/{doc_id}/void")
def hr_void_document(doc_id: str, account: HrAccount, db: Session = Depends(get_db)):
    doc = _doc_or_404(db, doc_id)
    err = documents.void(db, account.id, doc)
    return redirect(f"/hr/documents/{doc_id}", error=err, saved=None if err else "Document voided.")


@router.post("/documents/{doc_id}/offer")
def hr_offer_status(request: Request, doc_id: str, account: HrAccount, db: Session = Depends(get_db), status: str = Form(""),
                    reason: str = Form("")):
    doc = _doc_or_404(db, doc_id)
    err = documents.set_offer_status(db, account.id, doc, status)
    saved = None
    if not err:
        if status == "sent":
            emailed = notify.offer_sent(db, doc, documents.render(db, doc))
            saved = "Offer emailed to the candidate." if emailed else "Offer marked as sent (the offer email is turned off: send it yourself)."
        elif status == "accepted":
            notify.offer_accepted(db, notify.base_url(request), doc)
            saved = "Offer accepted. Next: create their staff account."
        elif status == "withdrawn":
            notify.offer_withdrawn(db, doc, " ".join(reason.split())[:255])
            saved = "Offer withdrawn."
        else:
            saved = "Offer marked as declined."
    return redirect(f"/hr/documents/{doc_id}", error=err, saved=saved)


@router.get("/documents/{doc_id}/print", response_class=HTMLResponse, name="hr_document_print")
def hr_print_document(request: Request, doc_id: str, account: HrAccount, db: Session = Depends(get_db)):
    doc = _doc_or_404(db, doc_id)
    return templates.TemplateResponse(request, "hr/letters/print.html", {
        "request": request, "letter": documents.render(db, doc), "title": documents.title(doc), "doc": doc})


@router.get("/documents/{doc_id}/pdf", name="hr_document_pdf")
def hr_document_pdf(doc_id: str, account: HrAccount, db: Session = Depends(get_db)):
    doc = _doc_or_404(db, doc_id)
    if doc.status == "void":
        return redirect(f"/hr/documents/{doc_id}", error="A void document can't be downloaded.")
    return pdf_download(db, [doc], f"/hr/documents/{doc_id}/print")
