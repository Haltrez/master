"""Storage for Role Match.

The real store is `app.pg.PgStore`. `LocalRoleRepo` keeps the same tables in
a local JSON file so the Role Match CLI works without a database; tests use
it too.

Rows are plain dicts with the column names from 003_role_match.sql.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from pathlib import Path
from typing import Any, Protocol

from .normalise import employer_norm
from .role_seed import ROLE_FAMILIES, SEED_EMPLOYERS

log = logging.getLogger("role_repo")

EMPLOYER_FILL_ONLY = ("website", "careers_url", "town", "lat", "lng", "ch_company_number", "sic_codes", "ats_type")


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def parse_ts(v: Any) -> dt.datetime | None:
    if v is None or isinstance(v, dt.datetime):
        return v
    return dt.datetime.fromisoformat(str(v).replace("Z", "+00:00"))


class RoleRepo(Protocol):
    def families(self) -> list[dict]: ...
    def employers(self) -> list[dict]: ...
    def add_employer(self, row: dict) -> dict: ...
    def update_employer(self, employer_id: int, fields: dict) -> None: ...
    def jobs(self, employer_id: int | None = None) -> list[dict]: ...
    def upsert_job(self, row: dict) -> dict: ...
    def update_job(self, job_id: int, fields: dict) -> None: ...
    def agency_by_name(self, name: str) -> dict | None: ...
    def create_agency(self, name: str) -> dict: ...
    def briefs(self, agency_id: int) -> list[dict]: ...
    def save_brief(self, row: dict) -> dict: ...
    def matches_for(self, brief_ids: list[int]) -> list[dict]: ...
    def upsert_match(self, row: dict) -> dict: ...
    def cache_get(self, key: str, max_age_days: float | None = None) -> Any: ...
    def cache_set(self, key: str, value: Any) -> None: ...
    def hot_lead_keys(self, keys: list[str]) -> set[str]: ...


def _jsonable(v: Any) -> Any:
    if isinstance(v, (dt.datetime, dt.date)):
        return v.isoformat()
    return v


class LocalRoleRepo:
    TABLES = ("role_families", "employers", "employer_jobs", "agencies", "candidate_briefs",
              "brief_matches", "http_cache", "hot_leads")

    def __init__(self, path: Path | None = None, *, clock=utcnow, seed: bool = True):
        self.path = path
        self.clock = clock
        self.t: dict[str, list[dict]] = {name: [] for name in self.TABLES}
        if path and path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            for name in self.TABLES:
                self.t[name] = data.get(name, [])
        elif seed:
            self._seed()

    # -- persistence ---------------------------------------------------------
    def save(self) -> None:
        if self.path:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.t, indent=1, default=_jsonable), encoding="utf-8")
            tmp.replace(self.path)

    def _now(self) -> str:
        return self.clock().isoformat()

    def _insert(self, table: str, row: dict) -> dict:
        row = {k: _jsonable(v) for k, v in row.items()}
        row["id"] = max((r["id"] for r in self.t[table]), default=0) + 1
        self.t[table].append(row)
        return row

    def _seed(self) -> None:
        for f in ROLE_FAMILIES:
            self._insert("role_families", dict(f))
        for e in SEED_EMPLOYERS:
            self.add_employer({**e, "ats_type": "html", "source": "manual", "status": "active"})

    # -- tables ----------------------------------------------------------------
    def families(self):
        return [dict(r) for r in self.t["role_families"]]

    def employers(self):
        return [dict(r) for r in self.t["employers"]]

    def add_employer(self, row):
        norm = employer_norm(row["name"])
        for r in self.t["employers"]:
            if r["name_norm"] == norm:
                for k in EMPLOYER_FILL_ONLY:
                    if row.get(k) not in (None, "") and r.get(k) in (None, ""):
                        r[k] = row[k]
                return dict(r)
        base = {"website": None, "careers_url": None, "ats_type": None, "town": None, "lat": None, "lng": None,
                "ch_company_number": None, "sic_codes": None, "status": "active", "last_checked_at": None,
                "check_every_days": 7, "created_at": self._now()}
        return dict(self._insert("employers", {**base, **row, "name_norm": norm}))

    def update_employer(self, employer_id, fields):
        for r in self.t["employers"]:
            if r["id"] == employer_id:
                r.update({k: _jsonable(v) for k, v in fields.items()})

    def jobs(self, employer_id=None):
        return [dict(r) for r in self.t["employer_jobs"] if employer_id is None or r["employer_id"] == employer_id]

    def upsert_job(self, row):
        now = self._now()
        for r in self.t["employer_jobs"]:
            if r["employer_id"] == row["employer_id"] and r["url"] == row["url"]:
                for k, v in row.items():
                    if v is not None or k in ("location_text",):
                        r[k] = _jsonable(v)
                r["last_seen_at"] = now
                return dict(r)
        base = {"lat": None, "lng": None, "wayback_first_seen": None, "page_hash": None, "date_posted": None,
                "keywords_found": [], "location_text": None}
        return dict(self._insert("employer_jobs", {**base, **row, "first_seen_at": now, "last_seen_at": now}))

    def update_job(self, job_id, fields):
        for r in self.t["employer_jobs"]:
            if r["id"] == job_id:
                r.update({k: _jsonable(v) for k, v in fields.items()})

    def agency_by_name(self, name):
        norm = employer_norm(name)
        return next((dict(r) for r in self.t["agencies"] if r["name_norm"] == norm), None)

    def create_agency(self, name):
        return dict(self._insert("agencies", {"name": name, "name_norm": employer_norm(name), "stage": "new",
                                              "created_at": self._now()}))

    def briefs(self, agency_id):
        return [dict(r) for r in self.t["candidate_briefs"] if r["agency_id"] == agency_id]

    def save_brief(self, row):
        for r in self.t["candidate_briefs"]:
            if (r["agency_id"] == row["agency_id"] and r["role_family_id"] == row["role_family_id"]
                    and r["location"].lower() == row["location"].lower()):
                r.update({k: _jsonable(v) for k, v in row.items()})
                return dict(r)
        return dict(self._insert("candidate_briefs", {"client_id": None, "seniority": None, "active": True,
                                                      "created_at": self._now(), **row}))

    def matches_for(self, brief_ids):
        ids = set(brief_ids)
        return [dict(r) for r in self.t["brief_matches"] if r["brief_id"] in ids]

    def upsert_match(self, row):
        for r in self.t["brief_matches"]:
            if r["brief_id"] == row["brief_id"] and r["employer_job_id"] == row["employer_job_id"]:
                r.update({k: row.get(k) for k in ("score", "reasons", "distance_miles", "signal", "open_since_label",
                                                  "location_label")})
                return dict(r)
        return dict(self._insert("brief_matches", {"status": "new", "first_matched_at": self._now(), **row}))

    def cache_get(self, key, max_age_days=None):
        for r in self.t["http_cache"]:
            if r["key"] == key:
                if max_age_days is not None and self.clock() - parse_ts(r["fetched_at"]) > dt.timedelta(days=max_age_days):
                    return None
                return r["value"]
        return None

    def cache_set(self, key, value):
        self.t["http_cache"] = [r for r in self.t["http_cache"] if r["key"] != key]
        self.t["http_cache"].append({"key": key, "value": value, "fetched_at": self._now()})

    def hot_lead_keys(self, keys):
        known = {r["lead_key"] for r in self.t["hot_leads"]}
        return known & set(keys)
