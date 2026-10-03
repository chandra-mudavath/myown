from fastapi.templating import Jinja2Templates
from markupsafe import Markup, escape

from app.core.assets import asset


# Each module keeps its own templates/ folder; names stay namespaced ("admin/index.html").
templates = Jinja2Templates(directory=[
    "app/platform/templates",
    "app/public/templates",
    "app/modules/client/templates",
    "app/modules/staff/templates",
    "app/modules/admin/templates",
    "app/modules/hr/templates",
])


def two_tone(text: str) -> Markup:
    """Landing-page heading style: first half in ink, second half in the brand accent.

    "Tax Summary & Fees" -> "Tax Summary <span class="cx-accent">& Fees</span>".
    A "|" marks the split explicitly: "My | Dashboard".
    """
    text = str(text or "")
    if "|" in text:
        head, tail = (part.strip() for part in text.split("|", 1))
    else:
        words = text.split()
        cut = len(words) // 2
        head, tail = " ".join(words[:cut]), " ".join(words[cut:])
    if not tail:
        return escape(head)
    lead = f"{escape(head)} " if head else ""
    return Markup(f'{lead}<span class="cx-accent">{escape(tail)}</span>')


templates.env.filters["two_tone"] = two_tone
templates.env.globals["asset"] = asset
