"""Catch email addresses that can't be the person's real inbox, at sign-up (client feedback, Nov 2026).

- Misspelt well-known domains (test@gmil.com, test@gmal.com, name@hotmial.com, name@gmail.con) are refused with a
  "Did you mean ...?" so the person fixes it before the account is made.
- Dummy and throwaway domains (example.com, mailinator.com, ...) and reserved test names (.test, .invalid, ...) are
  refused: we send codes, letters and tax documents to this address.
- An ending typed twice (kaarthi@gmail.com.com, name@acme.com.net) is refused with the single ending suggested;
  com.au, co.in and other country endings are fine.
- A well-known name with something added (sneha@23gmail.com, gmail23.com, mygmail.com) is refused the same way.
- The domain starts with a letter, its parts are joined by single dots, none starts or ends with a hyphen, and it
  ends in letters only (.com, .co.in).
- The part before the @ starts with a letter (12345@gmail.com is refused) and has only letters, numbers and . _ + -;
  after it, letters, numbers, dots and hyphens.

The address itself isn't confirmed with a code for clients (decision, Nov 2026); this only stops typos and fakes.
"""
from __future__ import annotations

import re

# Real domains people sign up with. A domain in this set is never treated as a typo of another one
# (mail.com, email.com and ymail.com are one letter away from gmail.com but are real).
KNOWN = frozenset({
    "gmail.com", "googlemail.com", "yahoo.com", "yahoo.co.in", "yahoo.co.uk", "yahoo.in", "ymail.com",
    "hotmail.com", "hotmail.co.uk", "outlook.com", "outlook.in", "live.com", "live.in", "msn.com",
    "icloud.com", "me.com", "mac.com", "aol.com", "protonmail.com", "proton.me", "zoho.com", "zohomail.in",
    "rediffmail.com", "mail.com", "email.com", "gmx.com", "gmx.net", "fastmail.com", "comcast.net", "att.net",
    "verizon.net", "sbcglobal.net", "cox.net", "charter.net", "covrenttax.com",
})

# Popular domains whose misspellings we catch, and how many slips still count as "the same name":
# one for short names (gmail.com), two for long ones (rediffmail.com).
TARGETS = ("gmail.com", "googlemail.com", "yahoo.com", "yahoo.co.in", "hotmail.com", "outlook.com", "icloud.com",
           "protonmail.com", "rediffmail.com", "comcast.net", "sbcglobal.net", "verizon.net", "covrenttax.com")

# ".com" typed wrong; none of these is a real ending
BAD_ENDINGS = ("con", "cmo", "ocm", "comm", "coom", "vom", "xom", "cim", "c0m", "cpm", "copm", "som")

# Names that only ever appear as themselves: "23gmail" or "gmail23" is a mistake, never another provider
# Endings that never follow one another: "com.com" or "com.net" is a slip (com.au and co.in are real)
GENERIC = frozenset({"com", "net", "org", "edu", "gov", "info", "biz", "co"})

BRANDS = ("gmail", "googlemail", "yahoo", "hotmail", "outlook", "icloud", "rediffmail", "protonmail", "covrenttax")

DUMMY = frozenset({
    "example.com", "example.org", "example.net", "test.com", "testing.com", "email.test", "domain.com",
    "mailinator.com", "yopmail.com", "guerrillamail.com", "guerrillamail.info", "sharklasers.com", "10minutemail.com",
    "tempmail.com", "temp-mail.org", "tempmail.net", "trashmail.com", "getnada.com", "dispostable.com",
    "fakeinbox.com", "maildrop.cc", "throwawaymail.com", "mailnesia.com", "mintemail.com", "emailondeck.com",
})
RESERVED_ENDINGS = ("test", "example", "invalid", "localhost", "local")

_LOCAL_RE = re.compile(r"^[A-Za-z0-9._+-]+$")
_DOMAIN_RE = re.compile(r"^[A-Za-z0-9.-]+$")


def _distance(a: str, b: str) -> int:
    """Edits (add, drop, change, or swap two neighbouring letters) that turn a into b."""
    rows = [list(range(len(b) + 1))]
    for i in range(1, len(a) + 1):
        row = [i] + [0] * len(b)
        for j in range(1, len(b) + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            row[j] = min(rows[i - 1][j] + 1, row[j - 1] + 1, rows[i - 1][j - 1] + cost)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                row[j] = min(row[j], rows[i - 2][j - 2] + 1)
        rows.append(row)
    return rows[-1][-1]


def suggestion(domain: str) -> str | None:
    """The well-known domain this one looks like a misspelling of, or None."""
    domain = domain.lower()
    if domain in KNOWN:
        return None
    name, _, ending = domain.rpartition(".")
    if ending in BAD_ENDINGS and name:
        fixed = f"{name}.com"
        return suggestion(fixed) or fixed
    labels = domain.split(".")
    for i in range(1, len(labels) - 1):  # gmail.com.com -> gmail.com
        if labels[i] in GENERIC and labels[i + 1] in GENERIC and labels[i] != "co":
            fixed = ".".join(labels[:i + 1])
            return suggestion(fixed) or fixed
    first, _, rest = domain.partition(".")
    for brand in BRANDS:  # 23gmail.com -> gmail.com
        if brand in first and first != brand:
            fixed = f"{brand}.{rest}" if rest else f"{brand}.com"
            return fixed if fixed in KNOWN else next((t for t in TARGETS if t.startswith(brand + ".")), fixed)
    if first in ("gmail", "googlemail"):  # Gmail has no country addresses: gmail.co, gmail.in are mistakes
        return f"{first}.com"
    if first in BRANDS:  # the right name with a country ending (yahoo.co.jp, hotmail.fr, outlook.de) is real
        return None
    best = None
    for target in TARGETS:
        d = _distance(domain, target)
        if 0 < d <= (1 if len(target) < 10 else 2) and (best is None or d < best[0]):
            best = (d, target)
    return best[1] if best else None


def clean(email: str | None) -> tuple[str, str | None]:
    """(lower-case address, error) for any email typed into the site: sign-up, change of sign-in email, accounts
    made by an admin or HR, referrals, spouse email. Ravi@Gmail.com is kept as ravi@gmail.com."""
    email = (email or "").strip().lower()
    if not email or email.count("@") != 1 or len(email) > 254:
        return email, "Enter a valid email address."
    return email, problem(email)


def problem(email: str) -> str | None:
    """Why this address can't be used for an account, or None when it's fine."""
    local, _, domain = email.strip().rpartition("@")
    domain = domain.lower()
    if local and not ("a" <= local[0].lower() <= "z"):
        return "Email must start with a letter, for example ravi123@gmail.com."
    if not _LOCAL_RE.match(local or "") or not _DOMAIN_RE.match(domain):
        return "Email can only have letters, numbers and . _ + - before the @, and letters, numbers, dots and hyphens after it."
    labels = domain.split(".")
    if (len(labels) < 2 or any(not part or part[0] == "-" or part[-1] == "-" for part in labels)
            or not domain[0].isalpha() or not (labels[-1].isalpha() and len(labels[-1]) >= 2)):
        fix = suggestion(domain) if all(labels) else None
        return (f"Please check your email. Did you mean {local}@{fix}?" if fix
                else "Please check the part after the @. It should look like gmail.com or yourcompany.co.in.")
    if domain in DUMMY or domain.rpartition(".")[2] in RESERVED_ENDINGS:
        return "Please use your real email address. We send your sign-in codes and tax documents to it."
    fix = suggestion(domain)
    if fix:
        return f"Please check your email. Did you mean {local}@{fix}?"
    return None
