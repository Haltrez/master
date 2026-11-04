"""Extra job sources for volume: Careerjet and Jooble (official partner APIs) and
Indeed via an Apify actor (the owner's choice; Apify runs the scraping).

Every source yields pages of jobs shaped like ReedJob, so the collector stores,
dedupes and classifies them the same way. Job ids are namespaced per source.
"""

from __future__ import annotations

import base64
import datetime as dt
import email.utils
import hashlib
import logging
import time
from collections.abc import Callable, Iterator
from typing import Any

import httpx

from .reed_client import ReedError

log = logging.getLogger("sources")

OFFSETS = {"jooble": 3 * 10**15, "careerjet": 4 * 10**15, "indeed": 5 * 10**15}


def namespaced_id(source: str, native: Any) -> int:
    h = int(hashlib.sha1(f"{source}:{native}".encode()).hexdigest()[:15], 16) % 10**15
    return OFFSETS[source] + h


class Job:
    def __init__(self, *, source: str, native_id: Any, employer_name: str, job_title: str, location_name: str | None,
                 job_url: str | None, posted_date: dt.date | None = None, minimum_salary: float | None = None,
                 maximum_salary: float | None = None, description: str | None = None, raw: dict | None = None,
                 source_says_repost: bool = False, lat: float | None = None, lng: float | None = None):
        self.job_id = namespaced_id(source, native_id)
        self.employer_id = None
        self.employer_name = (employer_name or "").strip()
        self.job_title = (job_title or "").strip()
        self.location_name = location_name
        self.job_url = job_url
        self.posted_date = posted_date
        self.expiration_date = None
        self.minimum_salary = minimum_salary
        self.maximum_salary = maximum_salary
        self.job_description = description          # used for agency wording, never stored
        self.raw = raw or {}
        self.source_says_repost = source_says_repost
        self.lat, self.lng = lat, lng


def _throttle(state: dict, interval: float, sleep: Callable, clock: Callable) -> None:
    last = state.get("last")
    if last is not None:
        wait = last + interval - clock()
        if wait > 0:
            sleep(wait)
    state["last"] = clock()


# ---------------------------------------------------------------------------
# Careerjet: GET https://search.api.careerjet.net/v4/query, Basic auth (key, empty password)
# ---------------------------------------------------------------------------
class CareerjetClient:
    name = "careerjet"
    URL = "https://search.api.careerjet.net/v4/query"

    def __init__(self, api_key: str, user_ip: str, *, max_pages: int = 10, transport=None,
                 min_interval: float = 1.0, sleep=time.sleep, clock=time.monotonic):
        if not api_key:
            raise ReedError("CAREERJET_API_KEY not set")
        self._http = httpx.Client(transport=transport, timeout=30.0, headers={
            "Authorization": "Basic " + base64.b64encode(f"{api_key}:".encode()).decode()})
        self.user_ip = user_ip or "127.0.0.1"
        self.max_pages = max_pages
        self._state: dict = {}
        self._interval, self._sleep, self._clock = min_interval, sleep, clock

    def pages(self, keyword: str, location: str, radius_miles: int, first_run: bool) -> Iterator[list[Job]]:
        for page in range(1, min(self.max_pages, 10) + 1):
            _throttle(self._state, self._interval, self._sleep, self._clock)
            params = {"locale_code": "en_GB", "keywords": keyword, "location": location, "radius": radius_miles,
                      "page_size": 100, "page": page, "sort": "date", "fragment_size": 300,
                      "user_ip": self.user_ip, "user_agent": "repost-radar/1.0"}
            try:
                r = self._http.get(self.URL, params=params)
            except httpx.HTTPError as exc:
                raise ReedError(f"Careerjet network error: {exc}") from exc
            log.info("careerjet kw=%r page=%d status=%d", keyword, page, r.status_code)
            if r.status_code in (401, 403):
                raise ReedError(f"Careerjet refused the request ({r.status_code}): check CAREERJET_API_KEY / user_ip")
            if r.is_error:
                raise ReedError(f"Careerjet returned {r.status_code}: {r.text[:200]}")
            body = r.json()
            if body.get("type") != "JOBS":
                return    # ambiguous location or no jobs
            jobs = []
            for j in body.get("jobs") or []:
                posted = None
                if j.get("date"):
                    try:
                        posted = email.utils.parsedate_to_datetime(j["date"].replace(",", ", ", 1)).date()
                    except (TypeError, ValueError):
                        posted = None
                yearly = j.get("salary_type") == "Y"
                native = f"{j.get('company')}|{j.get('title')}|{j.get('locations')}|{j.get('date')}"
                jobs.append(Job(source="careerjet", native_id=native, employer_name=j.get("company") or "",
                                job_title=j.get("title") or "", location_name=j.get("locations"),
                                job_url=j.get("url"), posted_date=posted,
                                minimum_salary=j.get("salary_min") if yearly else None,
                                maximum_salary=j.get("salary_max") if yearly else None,
                                description=j.get("description"),
                                raw={k: j.get(k) for k in ("title", "company", "locations", "date", "salary")}))
            jobs = [x for x in jobs if x.employer_name and x.job_title]
            yield jobs
            if page >= int(body.get("pages") or 1) or not jobs:
                return


# ---------------------------------------------------------------------------
# Jooble: POST https://uk.jooble.org/api/{key}  (UK key from uk.jooble.org/api/about)
# Free keys have a LIFETIME quota of 500 requests, so runs are heavily rationed.
# ---------------------------------------------------------------------------
class JoobleClient:
    name = "jooble"
    RADII_KM = (0, 4, 8, 16, 26, 40, 80)

    def __init__(self, api_key: str, *, host: str = "https://uk.jooble.org", max_requests: int = 5, transport=None,
                 min_interval: float = 1.0, sleep=time.sleep, clock=time.monotonic):
        if not api_key:
            raise ReedError("JOOBLE_API_KEY not set")
        self._http = httpx.Client(transport=transport, timeout=30.0)
        self.url = f"{host.rstrip('/')}/api/{api_key}"
        self.requests_left = max_requests
        self._state: dict = {}
        self._interval, self._sleep, self._clock = min_interval, sleep, clock

    def pages(self, keyword: str, location: str, radius_miles: int, first_run: bool) -> Iterator[list[Job]]:
        km = radius_miles * 1.609
        radius = min(self.RADII_KM, key=lambda r: (r < km, abs(r - km)))
        page = 1
        while self.requests_left > 0:
            self.requests_left -= 1
            _throttle(self._state, self._interval, self._sleep, self._clock)
            body = {"keywords": keyword, "location": location, "radius": str(radius), "page": page,
                    "ResultOnPage": 100, "companysearch": False}
            try:
                r = self._http.post(self.url, json=body)
            except httpx.HTTPError as exc:
                raise ReedError(f"Jooble network error: {exc}") from exc
            log.info("jooble kw=%r page=%d status=%d", keyword, page, r.status_code)   # key is in the URL: never logged
            if r.status_code == 403:
                raise ReedError("Jooble refused the key (403): use a UK key from uk.jooble.org/api/about")
            if r.is_error:
                raise ReedError(f"Jooble returned {r.status_code}")
            data = r.json()
            jobs = []
            for j in data.get("jobs") or []:
                updated = (j.get("updated") or "")[:10]
                jobs.append(Job(source="jooble", native_id=j.get("id"), employer_name=j.get("company") or "",
                                job_title=j.get("title") or "", location_name=j.get("location"),
                                job_url=j.get("link"), posted_date=dt.date.fromisoformat(updated) if updated else None,
                                description=j.get("snippet"),
                                raw={k: j.get(k) for k in ("id", "title", "company", "location", "updated", "source")}))
            jobs = [x for x in jobs if x.employer_name and x.job_title]
            yield jobs
            if len(data.get("jobs") or []) < 100:
                return
            page += 1


# ---------------------------------------------------------------------------
# Indeed via Apify actor (default valig~indeed-jobs-scraper): start a run, wait, read the dataset.
# Indeed marks reposts itself (isRepost), which is the hot-lead signal.
# ---------------------------------------------------------------------------
class ApifyIndeedClient:
    name = "indeed"
    API = "https://api.apify.com/v2"

    def __init__(self, token: str, *, actor: str = "valig~indeed-jobs-scraper", max_items: int = 300,
                 first_run_days: str = "14", daily_days: str = "3", transport=None, sleep=time.sleep,
                 max_wait_s: int = 900):
        if not token:
            raise ReedError("APIFY_TOKEN not set")
        self._http = httpx.Client(transport=transport, timeout=90.0, headers={"Authorization": f"Bearer {token}"})
        self.actor, self.max_items = actor.replace("/", "~"), max_items
        self.first_run_days, self.daily_days = first_run_days, daily_days
        self._sleep, self.max_wait_s = sleep, max_wait_s

    def _items(self, run_input: dict) -> list[dict]:
        r = self._http.post(f"{self.API}/acts/{self.actor}/runs", params={"waitForFinish": 60}, json=run_input)
        if r.status_code in (401, 403):
            raise ReedError("Apify refused the token")
        if r.is_error:
            raise ReedError(f"Apify start failed {r.status_code}: {r.text[:200]}")
        run = r.json()["data"]
        waited = 60
        while run.get("status") in ("READY", "RUNNING") and waited < self.max_wait_s:
            r = self._http.get(f"{self.API}/actor-runs/{run['id']}", params={"waitForFinish": 60})
            run = r.json()["data"]
            waited += 60
        if run.get("status") != "SUCCEEDED":
            raise ReedError(f"Apify run ended {run.get('status')}")
        r = self._http.get(f"{self.API}/datasets/{run['defaultDatasetId']}/items", params={"clean": "true", "format": "json"})
        if r.is_error:
            raise ReedError(f"Apify dataset read failed {r.status_code}")
        return r.json()

    def pages(self, keyword: str, location: str, radius_miles: int, first_run: bool) -> Iterator[list[Job]]:
        items = self._items({"country": "gb", "title": keyword, "location": location, "limit": self.max_items,
                             "datePosted": self.first_run_days if first_run else self.daily_days})
        log.info("apify indeed kw=%r location=%r items=%d", keyword, location, len(items))
        jobs = []
        for j in items:
            emp = j.get("employer") or {}
            loc = j.get("location") or {}
            sal = j.get("baseSalary") or {}
            yearly = (sal.get("unitOfWork") or "").upper() == "YEAR"
            posted = (j.get("datePublished") or j.get("dateOnIndeed") or "")[:10]
            jobs.append(Job(source="indeed", native_id=j.get("key") or j.get("url"), employer_name=emp.get("name") or "",
                            job_title=j.get("title") or "", location_name=loc.get("city") or loc.get("admin2Code"),
                            job_url=j.get("url") or j.get("jobUrl"),
                            posted_date=dt.date.fromisoformat(posted) if posted else None,
                            minimum_salary=sal.get("min") if yearly else None, maximum_salary=sal.get("max") if yearly else None,
                            description=((j.get("description") or {}).get("text") or "")[:1500],
                            source_says_repost=bool(j.get("isRepost")), lat=loc.get("latitude"), lng=loc.get("longitude"),
                            raw={"key": j.get("key"), "title": j.get("title"), "employer": emp.get("name"),
                                 "city": loc.get("city"), "datePublished": j.get("datePublished"),
                                 "isRepost": j.get("isRepost"), "website": emp.get("corporateWebsite")}))
        yield [x for x in jobs if x.employer_name and x.job_title]
