"""Shared bits for the HR workspace routes: page context, redirects with a message, and the employee lookup."""
from __future__ import annotations

import io
import logging
import zipfile
from urllib.parse import quote, urlencode

from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.orm import Session

from app.core.config import settings
from app.modules.hr.models.records import LeaveRequest
from app.modules.hr.services import hr_settings, people


def hr_ctx(request: Request, db: Session, account, **extra) -> dict:
    hr_settings.ensure_defaults(db)
    return {"request": request, "account": account, "hr": people.current_hr(db, account), "app_name": settings.APP_NAME,
            "pending_leaves": db.query(LeaveRequest.id).filter(LeaveRequest.status == "pending").count(), **extra}


def redirect(url: str, **params) -> RedirectResponse:
    """303 to url with ?saved= / ?error= added (before any #section)."""
    path, _, fragment = url.partition("#")
    query = urlencode({k: v for k, v in params.items() if v})
    if query:
        path += ("&" if "?" in path else "?") + query
    return RedirectResponse(path + (f"#{fragment}" if fragment else ""), status_code=303)


def pdf_download(db: Session, docs: list, fallback_url: str, zip_name: str = "") -> Response:
    """The document(s) as a PDF file download (several → one .zip). Used by HR, staff and admin.
    If this server can't make PDFs (no Chromium), go to the Print / Save as PDF page instead."""
    from app.modules.hr.services import documents
    from app.platform.services.pdf import PdfUnavailable
    try:
        files = documents.pdfs(db, docs)
    except PdfUnavailable:
        logging.getLogger(__name__).warning("PDF download unavailable: install Playwright Chromium on this server")
        return RedirectResponse(fallback_url, status_code=303)
    if zip_name:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for name, data in files:
                z.writestr(name, data)
        body, filename, media = buf.getvalue(), zip_name, "application/zip"
    else:
        (filename, body), media = files[0], "application/pdf"
    return Response(body, media_type=media, headers={
        "Content-Disposition": f"attachment; filename=\"{filename}\"; filename*=UTF-8''{quote(filename)}",
        "Cache-Control": "no-store"})


def employee_or_404(db: Session, staff_id: str):
    staff = people.employee(db, staff_id)
    if not staff:
        raise HTTPException(status_code=404, detail="Employee not found")
    return staff
