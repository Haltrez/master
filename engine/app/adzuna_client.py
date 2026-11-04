"""Adzuna API client (official). Adzuna aggregates many UK job boards.

Docs: https://developer.adzuna.com/docs/search
GET https://api.adzuna.com/v1/api/jobs/gb/search/{page}?app_id=&app_key=&what=&where=&distance=(km)
Adzuna's terms ask for attribution ("Jobs by Adzuna") and use of its redirect URLs.

Adzuna doesn't say whether an advertiser is an agency, so the collector
classifies by: manual overrides, employers Reed has told us are agencies,
a list of big UK agency brands, and the agency-name word list.
"""

from __future__ import annotations

import datetime as dt
import logging
import time
from collections.abc import Callable, Iterator
from typing import Any

import httpx

from .reed_client import CallBudget, ReedError

log = logging.getLogger("adzuna")

BASE_URL = "https://api.adzuna.com/v1/api/jobs/gb/search"
PAGE_SIZE = 50
ID_OFFSET = 10**15   # listings.job_id namespace for Adzuna ids, so they never collide with Reed ids


def adzuna_job_id(raw_id: Any) -> int:
    return ID_OFFSET + int(str(raw_id))


class AdzunaJob:
    """Shaped like ReedJob so the collector treats both the same."""

    def __init__(self, item: dict[str, Any]):
        self.raw = item
        self.job_id = adzuna_job_id(item["id"])
        self.employer_id = None
        self.employer_name = ((item.get("company") or {}).get("display_name") or "").strip()
        self.job_title = (item.get("title") or "").strip()
        self.location_name = (item.get("location") or {}).get("display_name")
        self.minimum_salary = item.get("salary_min")
        self.maximum_salary = item.get("salary_max")
        self.job_url = item.get("redirect_url")
        created = item.get("created")
        self.posted_date = dt.date.fromisoformat(created[:10]) if created else None
        self.expiration_date = None
        self.job_description = item.get("description")


class AdzunaClient:
    def __init__(self, app_id: str, app_key: str, *, min_interval: float = 2.5, max_tries: int = 4,
                 transport: httpx.BaseTransport | None = None, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic):
        if not app_id or not app_key:
            raise ReedError("ADZUNA_APP_ID / ADZUNA_APP_KEY not set")
        self._auth = {"app_id": app_id, "app_key": app_key}
        self._http = httpx.Client(transport=transport, timeout=30.0, headers={"Accept": "application/json"})
        self._interval, self._tries = min_interval, max_tries
        self._sleep, self._clock = sleep, clock
        self._last: float | None = None

    def close(self) -> None:
        self._http.close()

    def _get(self, page: int, params: dict[str, Any], budget: CallBudget | None) -> dict[str, Any]:
        for attempt in range(1, self._tries + 1):
            if budget is not None:
                budget.take()
            if self._last is not None:
                wait = self._last + self._interval - self._clock()
                if wait > 0:
                    self._sleep(wait)
            self._last = self._clock()
            started = self._clock()
            try:
                resp = self._http.get(f"{BASE_URL}/{page}", params={**self._auth, **params})
            except httpx.TransportError as exc:
                if attempt == self._tries:
                    raise ReedError(f"Adzuna network error: {exc}") from exc
                self._sleep(2 ** attempt)
                continue
            count = "-"
            if resp.is_success:
                count = len(resp.json().get("results") or [])
            # Never log app_id/app_key: log our own params only.
            log.info("adzuna GET page=%d params=%s status=%d results=%s duration_ms=%.0f", page, params,
                     resp.status_code, count, (self._clock() - started) * 1000)
            if resp.status_code == 429 or resp.status_code >= 500:
                if attempt == self._tries:
                    raise ReedError(f"Adzuna returned {resp.status_code} after {attempt} tries")
                self._sleep(2 ** attempt)
                continue
            if resp.status_code in (401, 403):
                raise ReedError("Adzuna rejected the credentials: check ADZUNA_APP_ID / ADZUNA_APP_KEY")
            if resp.is_error:
                raise ReedError(f"Adzuna returned {resp.status_code}: {resp.text[:200]}")
            return resp.json()
        raise ReedError("unreachable")  # pragma: no cover

    def iter_search_pages(self, keywords: str, location: str, radius_miles: int | None = 15,
                          min_salary: int | None = None, max_days_old: int = 60,
                          budget: CallBudget | None = None, max_pages: int = 10) -> Iterator[list[AdzunaJob]]:
        # title_only: Adzuna otherwise matches descriptions too ("Mortgage Broker Account Manager"
        # for "management accountant"). Newest first, so a capped run gets the fresh ads.
        params: dict[str, Any] = {"title_only": keywords, "where": location, "results_per_page": PAGE_SIZE,
                                  "max_days_old": max_days_old, "sort_by": "date", "content-type": "application/json"}
        if radius_miles:
            params["distance"] = round(radius_miles * 1.609)
        if min_salary:
            params["salary_min"] = min_salary
        for page in range(1, max_pages + 1):
            body = self._get(page, params, budget)
            results = body.get("results") or []
            jobs = []
            for item in results:
                try:
                    j = AdzunaJob(item)
                    if j.employer_name and j.job_title:
                        jobs.append(j)
                except (KeyError, ValueError) as exc:
                    log.warning("adzuna: skipped result: %s", exc)
            yield jobs
            if len(results) < PAGE_SIZE or page * PAGE_SIZE >= int(body.get("count") or 0):
                break
