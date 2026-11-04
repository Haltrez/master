"""Database access.

The real store is `app.pg.PgStore` (direct Postgres). `MemoryRepo` has the
same collector interface and upsert semantics, and is used by tests.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field
from typing import Any, Protocol


log = logging.getLogger("db")

UPSERT_BATCH = 500


@dataclass
class Search:
    id: int
    label: str
    keywords: list[str]
    location: str
    radius_miles: int = 15
    min_salary: int | None = None
    active: bool = True
    last_run_at: str | None = None

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> Search:
        return cls(
            id=row["id"],
            label=row["label"],
            keywords=list(row.get("keywords") or []),
            location=row["location"],
            radius_miles=row.get("radius_miles") or 15,
            min_salary=row.get("min_salary"),
            active=row.get("active", True),
            last_run_at=row.get("last_run_at"),
        )


class Repo(Protocol):
    def get_search(self, search_id: int) -> Search | None: ...
    def list_active_searches(self) -> list[Search]: ...
    def create_search(self, label: str, keywords: list[str], location: str,
                      radius_miles: int, min_salary: int | None) -> Search: ...
    def create_run(self, search_id: int) -> int: ...
    def finish_run(self, run_id: int, *, status: str, api_calls: int, listings_seen: int,
                   new_listings: int, error: str | None) -> None: ...
    def get_run(self, run_id: int) -> dict[str, Any] | None: ...
    def touch_search(self, search_id: int) -> None: ...
    def get_overrides(self) -> dict[str, bool]: ...
    def upsert_listings(self, search_id: int, rows: list[dict[str, Any]]) -> int: ...


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


class DBError(Exception):
    pass


@dataclass
class MemoryRepo:
    """In-memory stand-in mirroring the SQL semantics of 002_collect.sql."""

    searches: dict[int, Search] = field(default_factory=dict)
    runs: dict[int, dict[str, Any]] = field(default_factory=dict)
    listings: dict[int, dict[str, Any]] = field(default_factory=dict)
    listing_searches: set[tuple[int, int]] = field(default_factory=set)
    overrides: dict[str, bool] = field(default_factory=dict)
    clock: Any = None  # callable returning a datetime; defaults to utcnow

    def _ts(self) -> dt.datetime:
        return self.clock() if self.clock else dt.datetime.now(dt.timezone.utc)

    def get_search(self, search_id):
        return self.searches.get(search_id)

    def list_active_searches(self):
        return [s for _, s in sorted(self.searches.items()) if s.active]

    def create_search(self, label, keywords, location, radius_miles, min_salary):
        sid = max(self.searches, default=0) + 1
        s = Search(sid, label, list(keywords), location, radius_miles, min_salary)
        self.searches[sid] = s
        return s

    def create_run(self, search_id):
        rid = max(self.runs, default=0) + 1
        self.runs[rid] = {"id": rid, "search_id": search_id, "status": "running",
                          "started_at": self._ts(), "finished_at": None, "api_calls": 0,
                          "listings_seen": 0, "new_listings": 0, "error": None}
        return rid

    def finish_run(self, run_id, *, status, api_calls, listings_seen, new_listings, error):
        self.runs[run_id].update(status=status, finished_at=self._ts(), api_calls=api_calls,
                                 listings_seen=listings_seen, new_listings=new_listings, error=error)

    def get_run(self, run_id):
        return self.runs.get(run_id)

    def touch_search(self, search_id):
        self.searches[search_id].last_run_at = self._ts().isoformat()

    def get_overrides(self):
        return dict(self.overrides)

    def upsert_listings(self, search_id, rows):
        now = self._ts()
        new = 0
        for row in {r["job_id"]: r for r in rows}.values():
            existing = self.listings.get(row["job_id"])
            if existing is None:
                self.listings[row["job_id"]] = {**row, "first_seen_at": now, "last_seen_at": now}
                new += 1
            else:
                first = existing["first_seen_at"]
                existing.update({k: v for k, v in row.items() if v is not None or k not in (
                    "employer_id", "url", "posted_date", "expiration_date")})
                existing["first_seen_at"] = first
                existing["last_seen_at"] = now
            self.listing_searches.add((row["job_id"], search_id))
        return new
