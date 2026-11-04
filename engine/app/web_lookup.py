"""Find a company's website (Brave Search API) and verified business emails
(Hunter.io). Ported from the owner's Latvian enrichment script and adapted
for the UK. Both are official APIs; keys come from env vars.

Rule kept from the original: an email is only marked verified when Hunter
says 'valid'. Anything else is kept for a manual check and never exported.
"""

from __future__ import annotations

import logging
import re
import time
import unicodedata
from typing import Any
from urllib.parse import urlsplit

import httpx

from .normalise import employer_norm

log = logging.getLogger("web_lookup")

# Directories, social networks and job boards that are never the company's own site.
SKIP_DOMAINS = ("linkedin.", "facebook.", "instagram.", "twitter.", "x.com", "youtube.", "tiktok.",
                "wikipedia.", "crunchbase.", "glassdoor.", "indeed.", "reed.co.uk", "totaljobs.", "cv-library.",
                "monster.", "jobsite.", "adzuna.", "cwjobs.", "find-and-update.company-information",
                "company-information.service.gov.uk", "companieshouse", "endole.", "opencorporates.", "bizdb.",
                "companycheck.", "yell.", "thomsonlocal.", "192.com", "bloomberg.", "zoominfo.", "rocketreach.",
                "apollo.io", "dnb.com", "trustpilot.", "checkatrade.", "gov.uk", "nhs.uk", "google.", "bing.",
                "yelp.", "glassdoor", "ukjobs", "jobs.", "recruitment-agencies", "agencycentral.", "feefo.")
GENERIC = {"uk", "limited", "ltd", "plc", "llp", "group", "the", "and", "of", "solutions", "services", "service",
           "recruitment", "recruiting", "consultancy", "consulting", "consultants", "partners", "international",
           "global", "associates", "holdings", "company", "staffing", "resourcing", "personnel", "talent",
           "search", "selection", "people", "careers", "jobs", "north", "south", "east", "west", "england",
           "britain", "british", "london"}
CAREERS_PATH = re.compile(r"/(careers?|jobs?|vacanc(y|ies)|join-?us|work-?(for|with)-?us|current-?(roles|vacancies)|"
                          r"opportunities|recruitment)(/|$|\.)", re.I)


def translit(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s or "") if not unicodedata.combining(c))


def company_tokens(company: str) -> list[str]:
    return [w for w in translit(employer_norm(company)).split() if len(w) >= 3 and w not in GENERIC]


def _domain(url: str) -> str:
    return (urlsplit(url).hostname or "").lower().removeprefix("www.")


class Brave:
    URL = "https://api.search.brave.com/res/v1/web/search"

    def __init__(self, api_key: str, *, transport: httpx.BaseTransport | None = None, min_interval: float = 1.1,
                 sleep=time.sleep, clock=time.monotonic):
        self.api_key = api_key
        self._http = httpx.Client(transport=transport, timeout=25.0,
                                  headers={"X-Subscription-Token": api_key, "Accept": "application/json"})
        self._interval, self._sleep, self._clock, self._last = min_interval, sleep, clock, None

    def search(self, q: str, count: int = 12) -> list[dict[str, Any]]:
        if self._last is not None:
            wait = self._last + self._interval - self._clock()
            if wait > 0:
                self._sleep(wait)
        self._last = self._clock()
        try:
            r = self._http.get(self.URL, params={"q": q, "count": count, "country": "gb"})
        except httpx.HTTPError as exc:
            log.warning("brave search failed (%s): %s", q, type(exc).__name__)
            return []
        log.info("brave q=%r status=%d", q, r.status_code)
        if r.status_code != 200:
            return []
        return (r.json().get("web") or {}).get("results") or []

    def find_domain(self, company: str, town: str | None = None) -> str:
        """The company's own domain, or '' if no result is clearly theirs."""
        toks = company_tokens(company)
        if not toks:
            return ""
        q = f"{company} {town or ''} UK".replace("  ", " ")
        for res in self.search(q):
            d = _domain(res.get("url", ""))
            if not d or any(s in d for s in SKIP_DOMAINS):
                continue
            sld = d.split(".")[0].replace("-", "")
            # Only trust a domain whose name contains a distinctive word of the company name.
            if any(t in sld or (len(sld) >= 4 and sld in "".join(toks)) for t in toks):
                return d
        return ""

    def find_careers_url(self, company: str, town: str | None = None, domain: str | None = None) -> str:
        domain = domain or self.find_domain(company, town)
        if not domain:
            return ""
        for res in self.search(f"site:{domain} careers OR jobs OR vacancies"):
            url = res.get("url", "")
            if _domain(url).endswith(domain) and CAREERS_PATH.search(urlsplit(url).path or "/"):
                return url.split("#")[0]
        return ""


class Hunter:
    URL = "https://api.hunter.io/v2/email-finder"

    def __init__(self, api_key: str, *, transport: httpx.BaseTransport | None = None, store=None,
                 monthly_limit: int | None = None):
        self.api_key = api_key
        self._http = httpx.Client(transport=transport, timeout=25.0)
        self._store, self._limit = store, monthly_limit

    def _take(self) -> bool:
        """One request from this month's allowance (kept in the cache table). False once it's used up."""
        if self._store is None or self._limit is None:
            return True
        key = "hunter_used:" + __import__("datetime").date.today().strftime("%Y-%m")
        used = int((self._store.cache_get(key) or {}).get("n", 0))
        if used >= self._limit:
            log.info("hunter: monthly limit of %d reached, skipping", self._limit)
            return False
        self._store.cache_set(key, {"n": used + 1})
        return True

    def find_email(self, domain: str, first_name: str, last_name: str) -> dict[str, Any]:
        """{'email', 'status', 'score', 'verified'}; verified only when Hunter says 'valid'."""
        empty = {"email": "", "status": "not_found", "score": 0, "verified": False}
        if not (domain and first_name and last_name) or not self._take():
            return empty
        try:
            r = self._http.get(self.URL, params={"api_key": self.api_key, "domain": domain,
                                                 "first_name": translit(first_name), "last_name": translit(last_name)})
        except httpx.HTTPError as exc:
            log.warning("hunter lookup failed: %s", type(exc).__name__)
            return empty
        log.info("hunter domain=%s status=%d", domain, r.status_code)  # never log the key or the email
        if r.status_code != 200:
            return empty
        d = r.json().get("data") or {}
        email = d.get("email")
        if not email:
            return empty
        status = (d.get("verification") or {}).get("status") or "unknown"
        return {"email": email, "status": status, "score": d.get("score") or 0, "verified": status == "valid"}

    def domain_people(self, domain: str, department: str | None = None, limit: int = 10) -> list[dict[str, Any]]:
        """People Hunter knows at a domain (optionally one department: hr, executive, management...)."""
        if not domain or not self._take():
            return []
        params = {"api_key": self.api_key, "domain": domain, "type": "personal", "limit": limit}
        if department:
            params["department"] = department
        try:
            r = self._http.get("https://api.hunter.io/v2/domain-search", params=params)
        except httpx.HTTPError as exc:
            log.warning("hunter domain search failed: %s", type(exc).__name__)
            return []
        log.info("hunter domain-search domain=%s department=%s status=%d", domain, department, r.status_code)
        if r.status_code != 200:
            return []
        out = []
        for e in (r.json().get("data") or {}).get("emails") or []:
            status = (e.get("verification") or {}).get("status") or "unknown"
            out.append({"email": e.get("value"), "first_name": e.get("first_name") or "", "last_name": e.get("last_name") or "",
                        "position": e.get("position") or "", "confidence": e.get("confidence") or 0,
                        "status": status, "verified": status == "valid"})
        return [p for p in out if p["email"]]
