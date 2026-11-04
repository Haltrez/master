"""Companies House Public Data API (official, free key).

Docs: https://developer-specs.company-information.service.gov.uk/
Auth: HTTP Basic with the API key as username and an empty password.
Rate limit per the docs: 600 requests per 5 minutes; we stay at 1 per second.

Phase 2.5 uses advanced company search only (SIC codes + location) to fill
the employer research queue. Phase 3 adds company profile and officers.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

import httpx

log = logging.getLogger("companies_house")

BASE_URL = "https://api.company-information.service.gov.uk"


class CompaniesHouseError(Exception):
    pass


class CompaniesHouseClient:
    def __init__(self, api_key: str, *, transport: httpx.BaseTransport | None = None, min_interval: float = 1.0,
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic):
        if not api_key:
            raise CompaniesHouseError("COMPANIES_HOUSE_API_KEY is not set")
        self._http = httpx.Client(base_url=BASE_URL, auth=(api_key, ""), transport=transport, timeout=30.0)
        self._interval, self._sleep, self._clock = min_interval, sleep, clock
        self._last: float | None = None

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        if self._last is not None:
            wait = self._last + self._interval - self._clock()
            if wait > 0:
                self._sleep(wait)
        self._last = self._clock()
        resp = self._http.get(path, params=params)
        log.info("companies house GET %s params=%s status=%d", path, params, resp.status_code)
        if resp.status_code == 404:
            return {"items": []}
        if resp.status_code == 401:
            raise CompaniesHouseError("Companies House returned 401: check COMPANIES_HOUSE_API_KEY")
        if resp.is_error:
            raise CompaniesHouseError(f"Companies House returned {resp.status_code}: {resp.text[:200]}")
        return resp.json()

    def advanced_search(self, sic_codes: list[str], location: str | None = None, size: int = 100,
                        start_index: int = 0, company_status: str = "active") -> list[dict[str, Any]]:
        params: dict[str, Any] = {"sic_codes": ",".join(sic_codes), "company_status": company_status,
                                  "size": size, "start_index": start_index}
        if location:
            params["location"] = location
        return self._get("/advanced-search/companies", params).get("items") or []


def employer_rows_from_search(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for it in items:
        addr = it.get("registered_office_address") or {}
        rows.append({
            "name": it.get("company_name", "").strip(),
            "ch_company_number": it.get("company_number"),
            "sic_codes": it.get("sic_codes") or [],
            "town": addr.get("locality"),
            "source": "companies_house",
            "status": "research",
        })
    return [r for r in rows if r["name"]]


# ---------------------------------------------------------------------------
# Phase 3: company search, profile and officers (cached 30 days)
# ---------------------------------------------------------------------------

CACHE_DAYS = 30
EMAIL_OK_TYPES = {"ltd", "llp", "plc"}


class CachedCompaniesHouse:
    """Wraps the client with the 30-day http_cache from the store."""

    def __init__(self, client: CompaniesHouseClient, cache):
        self.client, self.cache = client, cache

    def _cached(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        key = "ch:" + path + "?" + "&".join(f"{k}={params[k]}" for k in sorted(params))
        hit = self.cache.cache_get(key, max_age_days=CACHE_DAYS)
        if hit is not None:
            return hit
        body = self.client._get(path, params)
        self.cache.cache_set(key, body)
        return body

    def search(self, name: str) -> list[dict[str, Any]]:
        return self._cached("/search/companies", {"q": name, "items_per_page": 20}).get("items") or []

    def profile(self, number: str) -> dict[str, Any]:
        return self._cached(f"/company/{number}", {})

    def officers(self, number: str) -> list[dict[str, Any]]:
        return self._cached(f"/company/{number}/officers", {"items_per_page": 100}).get("items") or []


def name_similarity(a: str, b: str) -> float:
    """0-1 similarity of two company names after normalisation (legal suffixes dropped)."""
    from difflib import SequenceMatcher

    from .normalise import employer_norm
    x, y = employer_norm(a), employer_norm(b)
    if not x or not y:
        return 0.0
    if x == y:
        return 1.0
    seq = SequenceMatcher(None, x, y).ratio()
    tx, ty = set(x.split()), set(y.split())
    jacc = len(tx & ty) / len(tx | ty)
    return round(max(seq, (seq + jacc) / 2), 3)


def parse_officer_name(raw: str) -> tuple[str, str, str]:
    """Companies House gives 'SMITH, John Paul' -> ('John Paul Smith', 'John', 'Smith')."""
    raw = " ".join((raw or "").split())
    if "," in raw:
        last, first = [p.strip() for p in raw.split(",", 1)]
    else:
        parts = raw.split()
        first, last = " ".join(parts[:-1]), parts[-1] if parts else ""
    # drop titles like "Mr", "Dr"
    first_parts = [p for p in first.split() if p.lower().strip(".") not in {"mr", "mrs", "ms", "miss", "dr", "sir"}]
    last = last.title() if last.isupper() else last
    first_name = first_parts[0].title() if first_parts and first_parts[0].isupper() else (first_parts[0] if first_parts else "")
    full = " ".join([p.title() if p.isupper() else p for p in first_parts] + [last]).strip()
    return full, first_name, last


def current_directors(officers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [o for o in officers if (o.get("officer_role") or "").lower() in ("director", "llp-designated-member",
                                                                               "llp-member", "managing-director")
            and not o.get("resigned_on")]
