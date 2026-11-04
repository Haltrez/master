"""Polite HTTP fetching for employers' own careers pages and ATS job boards.

Rules (non-negotiable, see BUILD_PROMPT.md Phase 2.5):
- robots.txt is checked with urllib.robotparser before every domain
- one user agent, identifying the bot
- at most 1 request per 5 seconds per domain (robots.txt requests included)
- at most 3 pages per employer per check (PageBudget)
- never a job board
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

log = logging.getLogger("polite")

USER_AGENT = "SmartEmployBot/1.0 (+https://smartemploy.io/bot)"
ROBOTS_AGENT = "SmartEmployBot"
MAX_PAGES_PER_EMPLOYER = 3
SECONDS_PER_DOMAIN = 5.0

# Job boards we must never fetch, even if an employer's "careers URL" points there.
FORBIDDEN_HOST_SUFFIXES = (
    "indeed.com", "indeed.co.uk", "totaljobs.com", "cv-library.co.uk", "linkedin.com",
    "reed.co.uk", "monster.co.uk", "monster.com", "glassdoor.com", "glassdoor.co.uk",
    "jobsite.co.uk", "adzuna.co.uk", "adzuna.com", "cwjobs.co.uk", "caterer.com",
    "fish4.co.uk", "simplyhired.co.uk", "jooble.org", "google.com",
)


class FetchRefused(Exception):
    """Base for requests we deliberately did not make."""


class RobotsDisallowed(FetchRefused):
    pass


class PageCapReached(FetchRefused):
    pass


class ForbiddenHost(FetchRefused):
    pass


class PageBudget:
    def __init__(self, max_pages: int = MAX_PAGES_PER_EMPLOYER):
        self.max_pages = max_pages
        self.used = 0

    def take(self, url: str) -> None:
        if self.used >= self.max_pages:
            raise PageCapReached(f"page cap of {self.max_pages} reached; not fetching {url}")
        self.used += 1


def host_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def is_forbidden_host(url: str) -> bool:
    host = host_of(url)
    return any(host == s or host.endswith("." + s) for s in FORBIDDEN_HOST_SUFFIXES)


def _longest_match_first(rp: RobotFileParser) -> None:
    """urllib.robotparser applies the first matching rule; RFC 9309 says the
    longest (most specific) match wins, with Allow winning ties. Reorder each
    group's rules so first-match gives the RFC answer. Without this,
    'Disallow: /' + 'Allow: /v/' would wrongly block /v/ pages."""
    entries = list(getattr(rp, "entries", []))
    if getattr(rp, "default_entry", None) is not None:
        entries.append(rp.default_entry)
    for entry in entries:
        entry.rulelines.sort(key=lambda r: (len(r.path), r.allowance), reverse=True)


class PoliteFetcher:
    def __init__(
        self,
        *,
        transport: httpx.BaseTransport | None = None,
        seconds_per_domain: float = SECONDS_PER_DOMAIN,
        timeout: float = 20.0,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._http = httpx.Client(
            transport=transport,
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/json;q=0.9,*/*;q=0.5",
                     "Accept-Language": "en-GB,en;q=0.8"},
        )
        self._interval = seconds_per_domain
        self._sleep = sleep
        self._clock = clock
        self._last_hit: dict[str, float] = {}
        self._robots: dict[str, RobotFileParser | None] = {}
        self.requests_made: list[str] = []

    def close(self) -> None:
        self._http.close()

    def _throttle(self, host: str) -> None:
        last = self._last_hit.get(host)
        if last is not None:
            wait = last + self._interval - self._clock()
            if wait > 0:
                self._sleep(wait)
        self._last_hit[host] = self._clock()

    def _raw_get(self, url: str) -> httpx.Response:
        host = host_of(url)
        self._throttle(host)
        started = self._clock()
        self.requests_made.append(url)
        resp = self._http.get(url)
        # A redirect to another host still counts as a hit on that host.
        final_host = host_of(str(resp.url))
        if final_host != host:
            self._last_hit[final_host] = self._clock()
        log.info("GET %s status=%d bytes=%d duration_ms=%.0f", url, resp.status_code,
                 len(resp.content), (self._clock() - started) * 1000)
        return resp

    def _robots_for(self, url: str) -> RobotFileParser | None:
        """None means 'disallow everything' (robots.txt unreachable or forbidden)."""
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin in self._robots:
            return self._robots[origin]
        rp = RobotFileParser()
        try:
            resp = self._raw_get(origin + "/robots.txt")
        except httpx.HTTPError as exc:
            log.warning("robots.txt for %s unreachable (%s); treating as disallowed", origin, type(exc).__name__)
            self._robots[origin] = None
            return None
        if resp.status_code in (401, 403):
            rp.disallow_all = True
        elif 400 <= resp.status_code < 500:
            rp.allow_all = True  # no robots.txt: crawling allowed
        elif resp.status_code >= 500:
            log.warning("robots.txt for %s returned %d; treating as disallowed", origin, resp.status_code)
            self._robots[origin] = None
            return None
        else:
            rp.parse(resp.text.splitlines())
            _longest_match_first(rp)
        self._robots[origin] = rp
        return rp

    def allowed(self, url: str) -> bool:
        rp = self._robots_for(url)
        return bool(rp and rp.can_fetch(ROBOTS_AGENT, url))

    def get(self, url: str, budget: PageBudget) -> httpx.Response:
        if is_forbidden_host(url):
            raise ForbiddenHost(f"{host_of(url)} is a job board; never fetched")
        if not self.allowed(url):
            raise RobotsDisallowed(f"robots.txt disallows {url}")
        budget.take(url)
        return self._raw_get(url)
