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
