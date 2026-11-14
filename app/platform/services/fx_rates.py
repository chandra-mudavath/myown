"""Market exchange rates for the currency converter on Make Payment → Pay in INR (client feedback, Nov 2026).

Rates come from open.er-api.com (free, no key, ~160 currencies, updated once a day), with USD as the base, and
are kept in memory for CACHE_SECONDS. If the service can't be reached, the last rates fetched are used; with none
yet, the converter says rates aren't available.

For information only: what the client actually pays in rupees is worked out at the admin's USD -> INR rate
(payments.inr_rate), not at these rates.
"""
from __future__ import annotations

import logging
import threading
import time

import httpx

log = logging.getLogger(__name__)

URL = "https://open.er-api.com/v6/latest/USD"
CACHE_SECONDS = 6 * 3600
RETRY_SECONDS = 300  # after a failed fetch, wait this long before trying again
_lock = threading.Lock()
_cache: dict = {"rates": None, "updated": "", "fetched_at": 0.0, "failed_at": 0.0}


def _fetch() -> tuple[dict[str, float], str] | None:
    try:
        response = httpx.get(URL, timeout=8)
        response.raise_for_status()
        body = response.json()
    except (httpx.HTTPError, ValueError):
        log.exception("Exchange rates could not be fetched from %s", URL)
        return None
    rates = body.get("rates") if body.get("result") == "success" else None
    if not isinstance(rates, dict) or "INR" not in rates:
        log.error("Exchange rates response had no usable rates: %s", str(body)[:200])
        return None
    clean = {code: float(v) for code, v in rates.items()
             if isinstance(code, str) and len(code) == 3 and code.isalpha() and isinstance(v, (int, float)) and v > 0}
    return clean, str(body.get("time_last_update_utc") or "")


def latest() -> dict | None:
    """{"base": "USD", "rates": {code: units per 1 USD}, "updated": "<when the provider last updated>"}, or None."""
    now = time.time()
    with _lock:
        fresh = _cache["rates"] and now - _cache["fetched_at"] < CACHE_SECONDS
        recently_failed = now - _cache["failed_at"] < RETRY_SECONDS
        if not fresh and not recently_failed:
            result = _fetch()
            if result:
                _cache.update(rates=result[0], updated=result[1], fetched_at=now)
            else:
                _cache["failed_at"] = now
        if not _cache["rates"]:
            return None
        return {"base": "USD", "rates": _cache["rates"], "updated": _cache["updated"]}
