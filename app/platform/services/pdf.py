"""HTML → PDF with headless Chromium (Playwright), so a PDF looks exactly like the page on screen and on paper:
same fonts, layout, page breaks and background watermark.

The HTML is self-contained (inline styles, data: images). The browser runs with JavaScript off and every
network request blocked, so nothing in a document can reach out or run code.

Server setup, once:  pip install playwright  →  playwright install chromium
(On a developer machine without Playwright's own Chromium, the installed Google Chrome is used.)
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)


class PdfUnavailable(RuntimeError):
    """Playwright or a Chromium browser is not installed on this server."""


def _launch(p):
    try:
        return p.chromium.launch()
    except Exception:  # no Playwright Chromium: try the installed Chrome
        try:
            return p.chromium.launch(channel="chrome")
        except Exception as e:
            raise PdfUnavailable("No Chromium browser for PDF generation") from e


def html_to_pdfs(pages: list[str]) -> list[bytes]:
    """One A4 PDF per HTML page, all with one browser start."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise PdfUnavailable("Playwright is not installed") from e
    out = []
    with sync_playwright() as p:
        browser = _launch(p)
        try:
            context = browser.new_context(java_script_enabled=False)
            context.route("**/*", lambda route: route.continue_() if route.request.url.startswith("data:") else route.abort())
            page = context.new_page()
            page.emulate_media(media="print")
            for html in pages:
                page.set_content(html, wait_until="load")
                out.append(page.pdf(format="A4", print_background=True, prefer_css_page_size=True))
        finally:
            browser.close()
    return out


def html_to_pdf(html: str) -> bytes:
    return html_to_pdfs([html])[0]
