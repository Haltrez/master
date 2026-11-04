"""Earliest Wayback Machine capture for a URL (CDX API), cached 30 days.

Coverage is patchy: no capture -> None, which the report shows as "unknown".
"""

from __future__ import annotations

import datetime as dt
import logging
import time
from collections.abc import Callable
from typing import Any

import httpx

from .geo import Cache

log = logging.getLogger("wayback")

CDX_URL = "https://web.archive.org/cdx/search/cdx"
CACHE_DAYS = 30


class Wayback:
    def __init__(self, cache: Cache, *, transport: httpx.BaseTransport | None = None,
                 min_interval: float = 1.0, timeout: float = 15.0, max_consecutive_failures: int = 2,
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic):
        self._cache = cache
        self._http = httpx.Client(transport=transport, timeout=timeout,
                                  headers={"User-Agent": "repost-radar/0.1"})
        self._interval = min_interval
        self._sleep, self._clock = sleep, clock
        self._last: float | None = None
        self._failures = 0
        self._max_failures = max_consecutive_failures
        self.disabled = False

    def _throttle(self) -> None:
        if self._last is not None:
            wait = self._last + self._interval - self._clock()
            if wait > 0:
                self._sleep(wait)
        self._last = self._clock()

    def first_capture(self, url: str | None) -> dt.date | None:
        if not url:
            return None
        key = "wayback:" + url
        cached = self._cache.cache_get(key, max_age_days=CACHE_DAYS)
        if cached is not None:
            return dt.date.fromisoformat(cached["date"]) if cached.get("date") else None
        if self.disabled:
            return None
        self._throttle()
        params: dict[str, Any] = {"url": url, "output": "json", "fl": "timestamp,statuscode",
                                  "filter": "statuscode:200", "limit": 1}
        try:
            resp = self._http.get(CDX_URL, params=params)
            resp.raise_for_status()
            rows = resp.json() if resp.content.strip() else []
        except (httpx.HTTPError, ValueError) as exc:
            self._failures += 1
            log.warning("wayback lookup for %s failed (%s); showing unknown", url, type(exc).__name__)
            if self._failures >= self._max_failures:
                self.disabled = True
                log.warning("wayback: %d failures in a row, skipping it for the rest of this run", self._failures)
            return None
        self._failures = 0
        # rows = [["timestamp","statuscode"], ["20190302123456","200"]] ; header row first
        date = None
        for row in rows[1:] if rows and rows[0] and rows[0][0] == "timestamp" else rows:
            ts = str(row[0])
            if len(ts) >= 8 and ts[:8].isdigit():
                date = dt.date(int(ts[:4]), int(ts[4:6]), int(ts[6:8]))
                break
        self._cache.cache_set(key, {"date": date.isoformat() if date else None})
        log.info("wayback %s -> %s", url, date or "no capture")
        return date


# ---------------------------------------------------------------------------
# Earlier postings of a company + role, from archived Totaljobs ad URLs:
#   https://www.totaljobs.com/job/<role-slug>/<company-slug>-job<id>
# We only query the Internet Archive; Totaljobs itself is never contacted.
# ---------------------------------------------------------------------------
import re as _re

_JOB_ID = _re.compile(r"-job(\d+)")
_LEGAL = ("ltd", "limited", "plc", "llp", "uk")


def slug(text: str) -> str:
    return _re.sub(r"[^a-z0-9]+", "-", (text or "").lower().replace("&", "-")).strip("-")


def company_slugs(name: str) -> list[str]:
    """'Euro Car Parks Ltd' -> ['euro-car-parks-ltd', 'euro-car-parks'] (Totaljobs uses both styles)."""
    full = slug(name)
    parts = full.split("-")
    while parts and parts[-1] in _LEGAL:
        parts = parts[:-1]
    bare = "-".join(parts)
    return [s for s in dict.fromkeys([full, bare]) if s]


def role_slugs(title: str, title_norm: str) -> list[str]:
    return [s for s in dict.fromkeys([slug(title_norm), slug(title)]) if s]


class ArchiveReposts:
    def __init__(self, wayback: Wayback):
        self.wb = wayback

    def _cdx_prefix(self, prefix: str) -> list[list[str]]:
        if self.wb.disabled:
            return []
        self.wb._throttle()
        try:
            r = self.wb._http.get(CDX_URL, params={"url": prefix, "matchType": "prefix", "output": "json",
                                                   "fl": "timestamp,original", "limit": 200})
            r.raise_for_status()
            rows = r.json() if r.content.strip() else []
        except (httpx.HTTPError, ValueError) as exc:
            self.wb._failures += 1
            if self.wb._failures >= self.wb._max_failures:
                self.wb.disabled = True
            log.warning("archive lookup %s failed (%s)", prefix, type(exc).__name__)
            return []
        self.wb._failures = 0
        return rows[1:] if rows and rows[0] and rows[0][0] == "timestamp" else rows

    def prior_postings(self, company: str, title: str, title_norm: str, before: dt.date) -> dict:
        """Distinct earlier job ids for this company + role captured before `before`."""
        key = f"archive:{slug(company)}|{slug(title_norm)}"
        cached = self.wb._cache.cache_get(key, max_age_days=CACHE_DAYS)
        if cached is None:
            found: dict[str, list[str]] = {}
            for rs in role_slugs(title, title_norm)[:2]:
                for cs in company_slugs(company):
                    for ts, original in self._cdx_prefix(f"totaljobs.com/job/{rs}/{cs}-job"):
                        m = _JOB_ID.search(original)
                        if m and (m.group(1) not in found or ts < found[m.group(1)][0]):
                            found[m.group(1)] = [ts, original.split("?")[0]]
                    if found:
                        break
                if found:
                    break
            cached = {"ads": found}
            self.wb._cache.cache_set(key, cached)
        ads = []
        for job_id, (ts, url) in cached["ads"].items():
            d = dt.date(int(ts[:4]), int(ts[4:6]), int(ts[6:8]))
            # Captured 2+ weeks before we first saw the current ad: an earlier posting, not a cross-post.
            if before - dt.timedelta(days=365) <= d <= before - dt.timedelta(days=14):
                ads.append((d, job_id, f"https://web.archive.org/web/{ts}/{url}"))
        ads.sort()
        return {"prior_count": len(ads), "oldest_capture": ads[0][0] if ads else None,
                "old_url": ads[-1][2] if ads else None}
