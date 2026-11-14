"""HR documents as PDF downloads, and the CovrentTax watermark on every document.

Chromium isn't needed here: the PDF maker is replaced with a stub; test_pdf_maker_real runs it for real
when a browser is installed."""
from __future__ import annotations

import io
import zipfile

import pytest
from fastapi.testclient import TestClient

from app.core.security import create_access_token
from app.main import app
from app.modules.hr.models.records import HrDocument
from app.modules.hr.services import documents, hr_settings
from app.platform.models.auth import AuthAccount
from app.platform.services import pdf

from tests.test_admin_creates_hr import _new_staff
from tests.test_auth_email import _account, env  # noqa: F401  (fixture)
from tests.test_hr_phases import OFFER, office  # noqa: F401  (fixture)

APPOINTMENT = {"kind": "APPOINTMENT", "employee_name": "Hema R", "employee_number": "EMP-000001", "designation": "Tax Preparer",
               "department": "Tax", "joining_date": "2025-01-06", "monthly_salary": "60000", "probation_months": "6",
               "notice_days": "30"}


@pytest.fixture()
def made(monkeypatch):
    """Stub PDF maker: records the HTML it was given and returns a fake PDF per page."""
    pages: list[str] = []

    def fake(htmls):
        pages.extend(htmls)
        return [b"%PDF-1.7 fake " + str(i).encode() for i, _ in enumerate(htmls)]
    monkeypatch.setattr(pdf, "html_to_pdfs", fake)
    return pages


def _doc_id(location: str) -> str:
    return location.split("/hr/documents/")[1].split("?")[0]


def test_generate_and_download_finalises_and_downloads(office, made):
    hr = office["hr"]
    r = hr.post("/hr/documents", data={**OFFER, "action": "download"}, follow_redirects=False)
    assert "download=1" in r.headers["location"]
    doc_id = _doc_id(r.headers["location"])
    db = office["env"]["Session"]()
    doc = db.get(HrDocument, doc_id)
    db.close()
    assert doc.status == "final" and doc.rendered_html and "DRAFT" not in doc.rendered_html
    page = hr.get(r.headers["location"]).text
    assert f'url=/hr/documents/{doc_id}/pdf' in page  # the download starts by itself

    r = hr.get(f"/hr/documents/{doc_id}/pdf")
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    assert r.content.startswith(b"%PDF")
    assert 'filename="OL-' in r.headers["content-disposition"] and "Offer-letter_Meena-Shah.pdf" in r.headers["content-disposition"]
    assert "Meena Shah" in made[-1] and "@page" in made[-1]  # made from the print page


def test_save_as_draft_still_works(office, made):
    hr = office["hr"]
    r = hr.post("/hr/documents", data={**OFFER, "action": "draft"}, follow_redirects=False)
    assert "download" not in r.headers["location"]
    page = hr.get(r.headers["location"]).text
    assert "DRAFT" in page and "/pdf" in page and "http-equiv" not in page
    # Missing required fields: nothing is made, the form comes back with the error
    r = hr.post("/hr/documents", data={**OFFER, "candidate_name": "", "action": "download"})
    assert r.status_code == 400 and "Candidate name is required." in r.text and not made


def test_no_browser_falls_back_to_print_page(office, monkeypatch):
    def unavailable(_):
        raise pdf.PdfUnavailable("none")
    monkeypatch.setattr(pdf, "html_to_pdfs", unavailable)
    hr = office["hr"]
    doc_id = _doc_id(hr.post("/hr/documents", data=OFFER, follow_redirects=False).headers["location"])
    r = hr.get(f"/hr/documents/{doc_id}/pdf", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == f"/hr/documents/{doc_id}/print"


def test_staff_and_admin_download_and_others_cannot(office, made):
    env, hr, staff, sid = office["env"], office["hr"], office["staff"], office["sid"]
    doc_id = _doc_id(hr.post("/hr/documents", data={**APPOINTMENT, "staff_id": sid, "action": "download"},
                             follow_redirects=False).headers["location"])
    assert f"/staff/hr/documents/{doc_id}/pdf" in staff.get("/staff/hr/documents").text
    r = staff.get(f"/staff/hr/documents/{doc_id}/pdf")
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    r = office["admin"].get(f"/admin/hr-records/documents/{doc_id}/pdf")
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    _new_staff(office["admin"], "kiran@covrenttax.com", "PREPARER")
    db = env["Session"]()
    db.query(AuthAccount).filter(AuthAccount.email == "kiran@covrenttax.com").update({"must_change_password": False})
    db.commit()
    db.close()
    other = TestClient(app)
    other.cookies.update({"access_token": create_access_token("kiran@covrenttax.com", "STAFF")})
    assert other.get(f"/staff/hr/documents/{doc_id}/pdf", headers={"accept": "application/json"}).status_code == 404
    # A void document isn't downloaded
    hr.post(f"/hr/documents/{doc_id}/void")
    assert "error=" in hr.get(f"/hr/documents/{doc_id}/pdf", follow_redirects=False).headers["location"]


def test_month_payslips_as_zip(office, made):
    hr = office["hr"]
    assert "error=" in hr.get("/hr/payslips/download?month=2026-11", follow_redirects=False).headers["location"]
    hr.post("/hr/payslips/generate", data={"month": "2026-11"})
    hr.post("/hr/payslips/finalise", data={"month": "2026-11"})
    assert "Download final payslips" in hr.get("/hr/payslips?month=2026-11").text
    r = hr.get("/hr/payslips/download?month=2026-11")
    assert r.headers["content-type"] == "application/zip" and "Payslips_2026-11.zip" in r.headers["content-disposition"]
    names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    assert len(names) == 1 and names[0].startswith("PS-") and names[0].endswith("_Payslip_2026-11_Hema-R.pdf")


def test_watermark_is_covrenttax_on_every_document(office):
    env, hr = office["env"], office["hr"]
    form = {**hr_settings.DEFAULTS, "company_name": "Some Other Name"}
    form.pop("emails_on")
    hr.post("/hr/settings", data=form)
    doc_id = _doc_id(hr.post("/hr/documents", data=OFFER, follow_redirects=False).headers["location"])
    page = hr.get(f"/hr/documents/{doc_id}").text
    assert documents.WATERMARK in page and "repeat-y" in page  # repeats down the page on screen
    assert b"CovrentTax</text>" in __import__("base64").b64decode(documents.WATERMARK.split(",", 1)[1])
    printed = hr.get(f"/hr/documents/{doc_id}/print").text
    assert "position: fixed" in printed  # one per printed / PDF page


def test_pdf_maker_real():
    """Runs headless Chromium for real when one is installed (skipped otherwise)."""
    html = "<html><body><p style='page-break-after:always'>one</p><p>two</p></body></html>"
    try:
        data = pdf.html_to_pdf(html)
    except pdf.PdfUnavailable:
        pytest.skip("no Chromium on this machine")
    assert data.startswith(b"%PDF")
