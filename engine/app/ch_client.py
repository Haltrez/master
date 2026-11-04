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
