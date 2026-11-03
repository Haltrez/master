"""Database access.

`SupabaseRepo` talks to Supabase's REST API (PostgREST) with the service
role key, server-side only. `MemoryRepo` has the same interface and the
same upsert semantics, and is used by tests.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

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


class SupabaseRepo:
    def __init__(self, url: str, service_role_key: str, *, transport: httpx.BaseTransport | None = None):
        if not url or not service_role_key:
            raise DBError("SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set")
        self._http = httpx.Client(
            base_url=f"{url.rstrip('/')}/rest/v1",
            headers={
                "apikey": service_role_key,
                "Authorization": f"Bearer {service_role_key}",
                "Content-Type": "application/json",
            },
            timeout=60.0,
            transport=transport,
        )

    def close(self) -> None:
        self._http.close()

    def _check(self, resp: httpx.Response) -> Any:
        if resp.is_error:
            raise DBError(f"Supabase {resp.request.method} {resp.request.url.path} "
                          f"returned {resp.status_code}: {resp.text[:300]}")
        if not resp.content:
            return None
        return resp.json()

    def get_search(self, search_id: int) -> Search | None:
        rows = self._check(self._http.get("/searches", params={"id": f"eq.{int(search_id)}", "select": "*"}))
        return Search.from_row(rows[0]) if rows else None

    def list_active_searches(self) -> list[Search]:
        rows = self._check(self._http.get("/searches", params={"active": "eq.true", "select": "*", "order": "id"}))
        return [Search.from_row(r) for r in rows]

    def create_search(self, label, keywords, location, radius_miles, min_salary) -> Search:
        rows = self._check(self._http.post(
            "/searches",
            json={"label": label, "keywords": keywords, "location": location,
                  "radius_miles": radius_miles, "min_salary": min_salary},
            headers={"Prefer": "return=representation"},
        ))
        return Search.from_row(rows[0])

    def create_run(self, search_id: int) -> int:
        rows = self._check(self._http.post(
            "/runs", json={"search_id": search_id, "status": "running"},
            headers={"Prefer": "return=representation"},
        ))
        return rows[0]["id"]

    def finish_run(self, run_id, *, status, api_calls, listings_seen, new_listings, error) -> None:
        self._check(self._http.patch(
            "/runs", params={"id": f"eq.{int(run_id)}"},
            json={"status": status, "finished_at": _now(), "api_calls": api_calls,
                  "listings_seen": listings_seen, "new_listings": new_listings, "error": error},
        ))

    def get_run(self, run_id: int) -> dict[str, Any] | None:
        rows = self._check(self._http.get("/runs", params={"id": f"eq.{int(run_id)}", "select": "*"}))
        return rows[0] if rows else None

    def touch_search(self, search_id: int) -> None:
        self._check(self._http.patch("/searches", params={"id": f"eq.{int(search_id)}"},
                                     json={"last_run_at": _now()}))

    def get_overrides(self) -> dict[str, bool]:
        rows = self._check(self._http.get("/employer_overrides", params={"select": "employer_norm,is_agency"}))
        return {r["employer_norm"]: bool(r["is_agency"]) for r in rows}

    def upsert_listings(self, search_id: int, rows: list[dict[str, Any]]) -> int:
        new = 0
        for i in range(0, len(rows), UPSERT_BATCH):
            batch = rows[i:i + UPSERT_BATCH]
            result = self._check(self._http.post(
                "/rpc/upsert_listings", json={"p_search_id": search_id, "p_rows": batch},
            ))
            new += int(result or 0)
        return new


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
