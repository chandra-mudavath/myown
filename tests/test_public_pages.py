"""Public site pages (no sign-in needed)."""
from fastapi.testclient import TestClient

from app.main import app


def test_disclaimer_page_is_linked_from_the_footer():
    """The footer's Disclaimer link went nowhere (href="#"); it opens its own page now (client feedback, Nov 2026)."""
    c = TestClient(app)
    page = c.get("/disclaimer")
    assert page.status_code == 200
    assert 'id="disclaimer"' in page.text and 'id="irs-circular-230-disclosure"' in page.text
    assert "IRS Circular 230" in page.text and "competent qualified advisors" in page.text
    home = c.get("/").text
    assert home.count('href="/disclaimer"') == 2 and '<a href="#">Disclaimer</a>' not in home
