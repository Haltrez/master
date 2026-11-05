"""Reed Jobseeker API client (official API only).

Docs: https://www.reed.co.uk/developers/jobseeker
Auth: HTTP Basic with the API key as username and an empty password.
"""

from __future__ import annotations

import datetime as dt
import logging
import time
from collections.abc import Callable, Iterator
from typing import Any, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator

log = logging.getLogger("reed")

BASE_URL = "https://www.reed.co.uk/api/1.0"
PAGE_SIZE = 100  # Reed's maximum resultsToTake

PostedBy = Literal["employer", "agency"]


class ReedError(Exception):
    pass


class CallCapExceeded(ReedError):
    pass


class CallBudget:
    """Hard cap on API calls for one run, shared across all keywords."""

    def __init__(self, max_calls: int, source: str = "Reed", env: str = "REED_MAX_CALLS_PER_RUN"):
        self.max_calls = max_calls
        self.used = 0
        self.source, self.env = source, env

    def take(self) -> None:
        if self.used >= self.max_calls:
            raise CallCapExceeded(
                f"{self.source} API call cap reached ({self.max_calls} calls this run). "
                f"Raise {self.env} in .env (a search needs about one call per job title) or narrow the search."
            )
        self.used += 1


def _parse_reed_date(value: Any) -> Any:
    """Reed returns dates as dd/mm/yyyy. Accept ISO too; blank -> None."""
    if value in (None, ""):
        return None
    if isinstance(value, str):
        v = value.strip()
        for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%Y-%m-%dT%H:%M:%S"):
            try:
                return dt.datetime.strptime(v, fmt).date()
            except ValueError:
                continue
    return value


class ReedJob(BaseModel):
    """One search result. Field names per the Reed docs; extra fields kept in raw."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    job_id: int = Field(alias="jobId")
    employer_id: int | None = Field(None, alias="employerId")
    employer_name: str = Field(alias="employerName")
    employer_profile_id: int | None = Field(None, alias="employerProfileId")
    employer_profile_name: str | None = Field(None, alias="employerProfileName")
    job_title: str = Field(alias="jobTitle")
    location_name: str | None = Field(None, alias="locationName")
    minimum_salary: float | None = Field(None, alias="minimumSalary")
    maximum_salary: float | None = Field(None, alias="maximumSalary")
    currency: str | None = None
    expiration_date: dt.date | None = Field(None, alias="expirationDate")
    posted_date: dt.date | None = Field(None, alias="date")
    job_description: str | None = Field(None, alias="jobDescription")
    applications: int | None = None
    job_url: str | None = Field(None, alias="jobUrl")

    raw: dict[str, Any] = Field(default_factory=dict, exclude=True)

    @field_validator("expiration_date", "posted_date", mode="before")
    @classmethod
    def _dates(cls, v: Any) -> Any:
        return _parse_reed_date(v)

    @classmethod
    def from_api(cls, item: dict[str, Any]) -> ReedJob:
        job = cls.model_validate(item)
        job.raw = item
        return job


def search_params(
    keywords: str,
    location: str,
    radius: int | None,
    min_salary: int | None,
    posted_by: PostedBy,
) -> dict[str, Any]:
    if posted_by not in ("employer", "agency"):
        raise ValueError(f"posted_by must be 'employer' or 'agency', got {posted_by!r}")
    params: dict[str, Any] = {
        "keywords": keywords,
        "locationName": location,
        "postedByDirectEmployer": "true" if posted_by == "employer" else "false",
        "postedByRecruitmentAgency": "true" if posted_by == "agency" else "false",
    }
    if radius is not None:
        params["distanceFromLocation"] = radius
    if min_salary:
        params["minimumSalary"] = min_salary
    return params


class ReedClient:
    def __init__(
        self,
        api_key: str,
        *,
        requests_per_second: float = 1.0,
        max_tries: int = 5,
        backoff_base: float = 1.0,
        base_url: str = BASE_URL,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ):
        if not api_key:
            raise ReedError("REED_API_KEY is not set")
        self._http = httpx.Client(
            base_url=base_url,
            auth=(api_key, ""),
            timeout=timeout,
            transport=transport,
            headers={"Accept": "application/json", "User-Agent": "repost-radar/0.1"},
        )
        self._interval = 1.0 / requests_per_second if requests_per_second > 0 else 0.0
        self._max_tries = max(1, max_tries)
        self._backoff_base = backoff_base
        self._sleep = sleep
        self._clock = clock
        self._last_request_at: float | None = None

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> ReedClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- low level ---------------------------------------------------------

    def _throttle(self) -> None:
        if self._last_request_at is not None and self._interval:
            wait = self._last_request_at + self._interval - self._clock()
            if wait > 0:
                self._sleep(wait)
        self._last_request_at = self._clock()

    def _backoff(self, attempt: int, resp: httpx.Response | None) -> float:
        delay = self._backoff_base * (2 ** (attempt - 1))
        if resp is not None:
            retry_after = resp.headers.get("Retry-After", "")
            if retry_after.isdigit():
                delay = max(delay, float(retry_after))
        return min(delay, 60.0)

    def _get(self, path: str, params: dict[str, Any] | None, budget: CallBudget | None) -> Any:
        for attempt in range(1, self._max_tries + 1):
            if budget is not None:
                budget.take()
            self._throttle()
            started = self._clock()
            resp: httpx.Response | None = None
            try:
                resp = self._http.get(path, params=params)
            except httpx.TransportError as exc:
                duration_ms = (self._clock() - started) * 1000
                log.warning(
                    "reed GET %s params=%s status=network-error error=%s duration_ms=%.0f attempt=%d",
                    path, params, type(exc).__name__, duration_ms, attempt,
                )
                if attempt == self._max_tries:
                    raise ReedError(f"Reed network error after {attempt} tries: {exc}") from exc
                self._sleep(self._backoff(attempt, None))
                continue

            duration_ms = (self._clock() - started) * 1000
            body: Any = None
            count: Any = "-"
            if resp.headers.get("content-type", "").startswith("application/json"):
                try:
                    body = resp.json()
                except ValueError:
                    body = None
                if isinstance(body, dict) and isinstance(body.get("results"), list):
                    count = len(body["results"])
                elif resp.is_success and body is not None:
                    count = 1
            log.info(
                "reed GET %s params=%s status=%d results=%s duration_ms=%.0f attempt=%d",
                path, params, resp.status_code, count, duration_ms, attempt,
            )

            if resp.status_code == 429 or resp.status_code >= 500:
                if attempt == self._max_tries:
                    raise ReedError(f"Reed returned {resp.status_code} after {attempt} tries")
                self._sleep(self._backoff(attempt, resp))
                continue
            if resp.status_code == 401:
                raise ReedError("Reed returned 401 Unauthorized: check REED_API_KEY")
            if resp.status_code >= 400:
                raise ReedError(f"Reed returned {resp.status_code}: {resp.text[:200]}")
            if body is None:
                try:
                    body = resp.json()
                except ValueError as exc:
                    raise ReedError("Reed returned a non-JSON response") from exc
            return body
        raise ReedError("unreachable")  # pragma: no cover

    # -- public ------------------------------------------------------------

    def search_raw_page(
        self,
        keywords: str,
        location: str,
        radius: int | None = 10,
        min_salary: int | None = None,
        posted_by: PostedBy = "employer",
        skip: int = 0,
        budget: CallBudget | None = None,
    ) -> dict[str, Any]:
        params = search_params(keywords, location, radius, min_salary, posted_by)
        params["resultsToTake"] = PAGE_SIZE
        params["resultsToSkip"] = skip
        body = self._get("/search", params, budget)
        if not isinstance(body, dict):
            raise ReedError("Unexpected Reed search response shape")
        return body

    def iter_search_pages(
        self,
        keywords: str,
        location: str,
        radius: int | None = 10,
        min_salary: int | None = None,
        posted_by: PostedBy = "employer",
        budget: CallBudget | None = None,
    ) -> Iterator[list[ReedJob]]:
        """Yield one parsed page at a time until a short page or the total is reached."""
        skip = 0
        while True:
            body = self.search_raw_page(keywords, location, radius, min_salary, posted_by, skip, budget)
            results = body.get("results") or []
            total = body.get("totalResults")
            jobs: list[ReedJob] = []
            for item in results:
                try:
                    jobs.append(ReedJob.from_api(item))
                except Exception as exc:  # keep going; one bad item shouldn't kill a run
                    log.warning("reed: skipped unparseable result jobId=%s: %s", item.get("jobId"), exc)
            yield jobs
            skip += len(results)
            if len(results) < PAGE_SIZE:
                break
            if isinstance(total, int) and skip >= total:
                break

    def search(
        self,
        keywords: str,
        location: str,
        radius: int | None = 10,
        min_salary: int | None = None,
        posted_by: PostedBy = "employer",
        budget: CallBudget | None = None,
    ) -> list[ReedJob]:
        out: list[ReedJob] = []
        for page in self.iter_search_pages(keywords, location, radius, min_salary, posted_by, budget):
            out.extend(page)
        return out

    def get_details(self, job_id: int, budget: CallBudget | None = None) -> dict[str, Any]:
        """Job details. Phase 2 calls this for newly hot leads only, never per listing."""
        body = self._get(f"/jobs/{int(job_id)}", None, budget)
        if not isinstance(body, dict):
            raise ReedError("Unexpected Reed details response shape")
        return body
