"""Geocoding with the free postcodes.io API, cached forever, and distances in miles."""

from __future__ import annotations

import logging
import math
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import quote

import httpx

log = logging.getLogger("geo")

BASE_URL = "https://api.postcodes.io"
UK_POSTCODE = re.compile(r"\b([A-Z]{1,2}\d[A-Z\d]?)\s*(\d[A-Z]{2})\b", re.IGNORECASE)
# Prefer bigger places when a name is ambiguous (Preston, Bolton, ...).
LOCAL_TYPE_RANK = {"City": 0, "Town": 1, "Other Settlement": 2, "Suburban Area": 3, "Village": 4, "Hamlet": 5}


@dataclass(frozen=True)
class Place:
    lat: float
    lng: float
    name: str
    region: str | None = None


class Cache(Protocol):
    def cache_get(self, key: str, max_age_days: float | None = None) -> Any: ...
    def cache_set(self, key: str, value: Any) -> None: ...


def miles_between(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lng1 = map(math.radians, a)
    lat2, lng2 = map(math.radians, b)
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lng2 - lng1) / 2) ** 2
    return 3958.8 * 2 * math.asin(math.sqrt(h))


def _variants(text: str) -> list[str]:
    """'Westhoughton, Bolton' -> ['Westhoughton', 'Bolton'];
    'Manchester City Centre' -> ['Manchester City Centre', 'Manchester City', 'Manchester']."""
    out: list[str] = []
    for part in text.split(","):
        words = re.sub(r"[^\w\s'-]", " ", part).split()
        for n in range(len(words), 0, -1):
            v = " ".join(words[:n])
            if v and v.lower() not in {o.lower() for o in out}:
                out.append(v)
    return out


class Geocoder:
    def __init__(self, cache: Cache, *, transport: httpx.BaseTransport | None = None,
                 min_interval: float = 0.2, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic):
        self._cache = cache
        self._http = httpx.Client(base_url=BASE_URL, transport=transport, timeout=15.0,
                                  headers={"User-Agent": "repost-radar/0.1"})
        self._interval = min_interval
        self._sleep, self._clock = sleep, clock
        self._last: float | None = None

    def _get(self, path: str, params: dict | None = None) -> Any:
        if self._last is not None:
            wait = self._last + self._interval - self._clock()
            if wait > 0:
                self._sleep(wait)
        self._last = self._clock()
        resp = self._http.get(path, params=params)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json().get("result")

    def _postcode(self, pc: str) -> Place | None:
        r = self._get(f"/postcodes/{quote(pc)}")
        if not r or r.get("latitude") is None:
            return None
        return Place(r["latitude"], r["longitude"], r["postcode"], r.get("region"))

    def _place(self, name: str) -> Place | None:
        results = self._get("/places", {"q": name, "limit": 100}) or []
        exact = [r for r in results if (r.get("name_1") or "").lower() == name.lower() and r.get("latitude") is not None]
        if not exact:
            return None
        best = min(exact, key=lambda r: LOCAL_TYPE_RANK.get(r.get("local_type") or "", 9))
        return Place(best["latitude"], best["longitude"], best["name_1"], best.get("region"))

    def geocode(self, text: str | None) -> Place | None:
        if not text or not text.strip():
            return None
        key = "geo:" + " ".join(text.lower().split())
        cached = self._cache.cache_get(key)
        if cached is not None:
            return Place(**cached) if cached else None
        place: Place | None = None
        try:
            m = UK_POSTCODE.search(text)
            if m:
                place = self._postcode(f"{m.group(1)}{m.group(2)}".upper())
            if place is None:
                for v in _variants(UK_POSTCODE.sub(" ", text)):
                    place = self._place(v)
                    if place:
                        break
        except httpx.HTTPError as exc:
            log.warning("geocode '%s' failed (%s); not cached", text, type(exc).__name__)
            return None
        # Cache forever, including definite misses (stored as {}).
        self._cache.cache_set(key, place.__dict__ if place else {})
        log.info("geocode '%s' -> %s", text, f"{place.name} ({place.lat:.3f},{place.lng:.3f})" if place else "not found")
        return place
