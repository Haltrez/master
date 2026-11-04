"""TheirStack job postings API (licensed data; aggregates major boards incl. Indeed/LinkedIn).

Docs: https://theirstack.com/en/docs/api-reference/jobs/search_jobs_v1
POST https://api.theirstack.com/v1/jobs/search, Authorization: Bearer <key>
Costs 1 credit per job returned (free plan: 200/month, 25 per page, 5 pages),
so every run has a hard job cap. There's no radius filter: we ask for UK jobs
with matching titles and keep those near the search location ourselves.

Field names are read defensively (several are optional in their schema).
"""

from __future__ import annotations

import datetime as dt
import logging
import time
from collections.abc import Callable, Iterator
from typing import Any

import httpx

from .reed_client import ReedError

log = logging.getLogger("theirstack")

URL = "https://api.theirstack.com/v1/jobs/search"
ID_OFFSET = 2 * 10**15   # listings.job_id namespace (Reed < 10^9, Adzuna 10^15+)


def theirstack_job_id(raw: Any) -> int:
    return ID_OFFSET + int(str(raw))


def _date(v: Any) -> dt.date | None:
    if not v:
        return None
    try:
        return dt.date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


class TheirStackJob:
    """Shaped like ReedJob/AdzunaJob for the collector."""

    def __init__(self, item: dict[str, Any]):
        self.raw = {k: v for k, v in item.items() if k not in ("description", "hiring_team")}  # no free text / people
        self.job_id = theirstack_job_id(item["id"])
        company = item.get("company_object") or {}
        self.employer_id = None
        self.employer_name = (item.get("company") or company.get("name") or "").strip()
        self.company_domain = item.get("company_domain") or company.get("domain")
        self.job_title = (item.get("job_title") or "").strip()
        self.location_name = item.get("short_location") or item.get("location") or item.get("long_location")
        self.lat = item.get("latitude")
        self.lng = item.get("longitude")
        self.minimum_salary = item.get("min_annual_salary") or item.get("min_salary")
        self.maximum_salary = item.get("max_annual_salary") or item.get("max_salary")
        self.job_url = item.get("final_url") or item.get("url") or item.get("source_url")
        # A repost keeps the original posted date, so "open since" reflects the first posting.
        self.posted_date = _date(item.get("date_posted"))
        self.reposted = bool(item.get("reposted")) or bool(item.get("date_reposted"))
        self.expiration_date = None
        self.job_description = None


class TheirStackClient:
    def __init__(self, api_key: str, *, page_size: int = 25, min_interval: float = 0.4, max_tries: int = 4,
                 transport: httpx.BaseTransport | None = None, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic):
        if not api_key:
            raise ReedError("THEIRSTACK_API_KEY not set")
        self._http = httpx.Client(transport=transport, timeout=60.0,
                                  headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"})
        self.page_size = page_size
        self._interval, self._tries, self._sleep, self._clock = min_interval, max_tries, sleep, clock
        self._last: float | None = None
        self._company_type_ok = True

    def close(self) -> None:
        self._http.close()

    def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        for attempt in range(1, self._tries + 1):
            if self._last is not None:
                wait = self._last + self._interval - self._clock()
                if wait > 0:
                    self._sleep(wait)
            self._last = self._clock()
            try:
                r = self._http.post(URL, json=body)
            except httpx.TransportError as exc:
                if attempt == self._tries:
                    raise ReedError(f"TheirStack network error: {exc}") from exc
                self._sleep(2 ** attempt)
                continue
            log.info("theirstack page=%s titles=%s status=%d", body.get("page"), body.get("job_title_or"), r.status_code)
            if r.status_code == 429 or r.status_code >= 500:
                if attempt == self._tries:
                    raise ReedError(f"TheirStack returned {r.status_code} after {attempt} tries")
                self._sleep(2 ** attempt)
                continue
            if r.status_code == 402:
                raise ReedError("TheirStack: out of credits (402)")
            if r.status_code in (401, 403):
                raise ReedError("TheirStack rejected the API key")
            if r.status_code == 422 and "company_type" in body:
                # Not every plan/version accepts company_type on job search: retry without it.
                self._company_type_ok = False
                body = {k: v for k, v in body.items() if k != "company_type"}
                continue
            if r.is_error:
                raise ReedError(f"TheirStack returned {r.status_code}: {r.text[:200]}")
            return r.json()
        raise ReedError("unreachable")  # pragma: no cover

    def iter_search_pages(self, keywords: list[str], *, max_age_days: int, max_jobs: int,
                          country: str = "GB") -> Iterator[list[TheirStackJob]]:
        fetched = 0
        page = 0
        while fetched < max_jobs:
            limit = min(self.page_size, max_jobs - fetched)
            body: dict[str, Any] = {"job_title_or": keywords, "job_country_code_or": [country],
                                    "posted_at_max_age_days": max_age_days, "page": page, "limit": limit,
                                    "include_total_results": False}
            if self._company_type_ok:
                body["company_type"] = "direct_employer"
            data = (self._post(body).get("data") or [])
            jobs = []
            for item in data:
                try:
                    j = TheirStackJob(item)
                    if j.employer_name and j.job_title:
                        jobs.append(j)
                except (KeyError, ValueError) as exc:
                    log.warning("theirstack: skipped result: %s", exc)
            fetched += len(data)
            yield jobs
            if len(data) < limit:
                break
            page += 1
