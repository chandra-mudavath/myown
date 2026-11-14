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


def plain_number(value) -> str:
    """A count of days or a quantity without trailing zeros or exponents: 30, 2.5, 0 (never 3E+1 or 12.0)."""
    try:
        text = f"{float(value):.2f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        return str(value or "")
    return "0" if text in ("-0", "") else text


templates.env.filters["two_tone"] = two_tone
templates.env.filters["num"] = plain_number
templates.env.globals["asset"] = asset


def signed_money(value) -> str:
    """$4,598.00, or ($184.00) for a negative amount (a tax due). Used by the Tax Summary table."""
    from decimal import Decimal
    value = Decimal(str(value or 0))
    return f"(${abs(value):,.2f})" if value < 0 else f"${value:,.2f}"


templates.env.filters["signed_money"] = signed_money
