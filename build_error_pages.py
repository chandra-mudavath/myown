"""Write the error pages the web server shows when the app can't answer: 413 (file too large), 502 (app down)
and 504 (too slow). They use the same template as the in-app screens, with the CSS inlined, because
nothing can be loaded from the app while it is down.

Run after changing errors/error.html, errors.css or the texts in app/core/errors.py:

    python build_error_pages.py            # writes deploy/error-pages/413.html, 502.html, 504.html

nginx (inside the server block):

    client_max_body_size 32m;              # MAX_UPLOAD_SIZE_MB (30) plus room for the rest of the form
    error_page 413 /_error_pages/413.html;
    error_page 502 503 /_error_pages/502.html;
    error_page 504 /_error_pages/504.html;
    location ^~ /_error_pages/ { internal; alias /srv/covrenttax/deploy/error-pages/; }
"""
from datetime import datetime
from pathlib import Path

from app.core.config import settings
from app.core.errors import SCREENS, _Blank
from app.core.templates import templates

OUT = Path("deploy/error-pages")
STATIC = Path("app/platform/static")
PAGES = {413: "SYS-008", 502: "SYS-009", 504: "SYS-010"}
ACTIONS = {
    "back": {"label": "Go back", "href": "/", "onclick": "if (history.length > 1) { history.back(); return false; }"},
    "reload": {"label": "Try again", "href": "/", "onclick": "location.reload(); return false;"},
    "dashboard": {"label": "Home", "href": "/"},
}


def build() -> list[Path]:
    css = (STATIC / "css/theme.css").read_text(encoding="utf-8") + "\n" + (STATIC / "css/errors.css").read_text(encoding="utf-8")
    js = (STATIC / "js/theme-init.js").read_text(encoding="utf-8")
    values = _Blank(app_name=settings.APP_NAME, max_mb=settings.MAX_UPLOAD_SIZE_MB)
    OUT.mkdir(parents=True, exist_ok=True)
    written = []
    for status, code in PAGES.items():
        screen = SCREENS[code]
        html = templates.get_template("errors/error.html").render(
            app_name=settings.APP_NAME, screen=screen, title=screen.title.format_map(values),
            problem=screen.problem.format_map(values), fix=screen.fix.format_map(values),
            actions=[ACTIONS[a] for a in screen.actions], support_email=settings.EMAIL_REPLY_TO,
            year=datetime.now().year, inline_css=css, inline_js=js)
        path = OUT / f"{status}.html"
        path.write_text(html, encoding="utf-8")
        written.append(path)
    return written


if __name__ == "__main__":
    for p in build():
        print(f"wrote {p}")
