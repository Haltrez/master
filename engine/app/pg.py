"""Direct Postgres access for the whole engine (psycopg 3).

Works against Supabase (use the connection string from Project Settings ->
Database) or a plain Postgres on the Hetzner box. Server side only.

PgStore implements the Phase 1 collector interface (db.Repo), the Role Match
interface (role_repo.RoleRepo) and the queries used by detection, agencies,
the API and the CRM.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import threading
from collections.abc import Iterable, Sequence
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .db import Search
from .normalise import employer_norm

log = logging.getLogger("pg")

UPSERT_BATCH = 500
EMPLOYER_FILL_ONLY = ("website", "careers_url", "town", "lat", "lng", "ch_company_number", "sic_codes", "ats_type")


def email_hash(email: str) -> str:
    return hashlib.sha256(email.strip().lower().encode("utf-8")).hexdigest()


def _jsonable(v: Any) -> Any:
    if isinstance(v, (dt.datetime, dt.date)):
        return v.isoformat()
    if isinstance(v, (list, dict)):
        return v
    return v


def _plain(row: dict | None) -> dict | None:
    """Rows as plain JSON-friendly dicts (Decimal -> float, dates kept)."""
    if row is None:
        return None
    from decimal import Decimal
    return {k: (float(v) if isinstance(v, Decimal) else v) for k, v in row.items()}


class PgStore:
    def __init__(self, dsn: str):
        if not dsn:
            raise ValueError("DATABASE_URL is not set")
        self.dsn = dsn
        self._local = threading.local()

    # -- connection ------------------------------------------------------------
    @property
    def conn(self) -> psycopg.Connection:
        c = getattr(self._local, "conn", None)
        if c is None or c.closed:
            c = psycopg.connect(self.dsn, autocommit=True, row_factory=dict_row)
            self._local.conn = c
        return c

    def close(self) -> None:
        c = getattr(self._local, "conn", None)
        if c is not None and not c.closed:
            c.close()

    def q(self, sql: str, params: Sequence | dict | None = None) -> list[dict]:
        with self.conn.cursor() as cur:
            cur.execute(sql, params)
            if cur.description is None:
                return []
            return [_plain(r) for r in cur.fetchall()]

    def one(self, sql: str, params: Sequence | dict | None = None) -> dict | None:
        rows = self.q(sql, params)
        return rows[0] if rows else None

    def val(self, sql: str, params: Sequence | dict | None = None) -> Any:
        r = self.one(sql, params)
        return next(iter(r.values())) if r else None

    def save(self) -> None:  # writes are immediate
        pass

    def ping(self) -> bool:
        return self.val("select 1 as ok") == 1

    def _update(self, table: str, row_id: int, fields: dict, key: str = "id") -> dict | None:
        if not fields:
            return self.one(f"select * from {table} where {key} = %s", (row_id,))
        cols = ", ".join(f"{k} = %s" for k in fields)
        vals = [Jsonb(v) if isinstance(v, dict) else v for v in fields.values()]
        return self.one(f"update {table} set {cols} where {key} = %s returning *", (*vals, row_id))

    def _insert(self, table: str, fields: dict) -> dict:
        cols = ", ".join(fields)
        ph = ", ".join(["%s"] * len(fields))
        vals = [Jsonb(v) if isinstance(v, dict) else v for v in fields.values()]
        return self.one(f"insert into {table} ({cols}) values ({ph}) returning *", vals)

    # =========================================================================
    # Phase 1: collector (db.Repo)
    # =========================================================================
    def get_search(self, search_id: int) -> Search | None:
        r = self.one("select * from searches where id = %s", (search_id,))
        return Search.from_row(r) if r else None

    def list_active_searches(self) -> list[Search]:
        return [Search.from_row(r) for r in self.q("select * from searches where active order by id")]

    def create_search(self, label, keywords, location, radius_miles, min_salary) -> Search:
        r = self._insert("searches", {"label": label, "keywords": list(keywords), "location": location,
                                      "radius_miles": radius_miles, "min_salary": min_salary})
        return Search.from_row(r)

    def create_run(self, search_id: int) -> int:
        return self.val("insert into runs (search_id, status) values (%s, 'running') returning id", (search_id,))

    def finish_run(self, run_id, *, status, api_calls, listings_seen, new_listings, error) -> None:
        self.q("update runs set status=%s, finished_at=now(), api_calls=%s, listings_seen=%s, new_listings=%s, "
               "error=%s where id=%s", (status, api_calls, listings_seen, new_listings, error, run_id))

    def get_run(self, run_id: int) -> dict | None:
        return self.one("select * from runs where id = %s", (run_id,))

    def touch_search(self, search_id: int) -> None:
        self.q("update searches set last_run_at = now() where id = %s", (search_id,))

    def get_overrides(self) -> dict[str, bool]:
        return {r["employer_norm"]: r["is_agency"] for r in self.q("select * from employer_overrides")}

    def upsert_listings(self, search_id: int, rows: list[dict]) -> int:
        new = 0
        for i in range(0, len(rows), UPSERT_BATCH):
            batch = [{k: _jsonable(v) for k, v in r.items()} for r in rows[i:i + UPSERT_BATCH]]
            new += int(self.val("select upsert_listings(%s, %s) as n", (search_id, Jsonb(batch))) or 0)
        return new

    # =========================================================================
    # Phase 2.5: Role Match (role_repo.RoleRepo)
    # =========================================================================
    def families(self):
        return self.q("select * from role_families order by id")

    def employers(self):
        return self.q("select * from employers order by id")

    def add_employer(self, row: dict) -> dict:
        norm = employer_norm(row["name"])
        cur = self.one("select * from employers where name_norm = %s", (norm,))
        if cur:
            fill = {k: row[k] for k in EMPLOYER_FILL_ONLY if row.get(k) not in (None, "") and cur.get(k) in (None, "")}
            return self._update("employers", cur["id"], fill) if fill else cur
        fields = {k: v for k, v in row.items() if v is not None}
        fields.setdefault("source", "manual")
        return self._insert("employers", {**fields, "name_norm": norm})

    def update_employer(self, employer_id: int, fields: dict) -> None:
        self._update("employers", employer_id, fields)

    def jobs(self, employer_id: int | None = None):
        if employer_id is None:
            return self.q("select * from employer_jobs order by id")
        return self.q("select * from employer_jobs where employer_id = %s order by id", (employer_id,))

    def upsert_job(self, row: dict) -> dict:
        fields = {k: v for k, v in row.items() if v is not None or k == "location_text"}
        cols = list(fields)
        updates = ", ".join(f"{c} = excluded.{c}" for c in cols if c not in ("employer_id", "url"))
        sql = (f"insert into employer_jobs ({', '.join(cols)}) values ({', '.join(['%s'] * len(cols))}) "
               f"on conflict (employer_id, url) do update set {updates}{', ' if updates else ''}last_seen_at = now() "
               "returning *")
        return self.one(sql, list(fields.values()))

    def update_job(self, job_id: int, fields: dict) -> None:
        self._update("employer_jobs", job_id, fields)

    def agency_by_name(self, name: str) -> dict | None:
        return self.one("select * from agencies where name_norm = %s", (employer_norm(name),))

    def create_agency(self, name: str) -> dict:
        return self.one("insert into agencies (name, name_norm) values (%s, %s) on conflict (name_norm) do update "
                        "set name_norm = excluded.name_norm returning *", (name, employer_norm(name)))

    def briefs(self, agency_id: int):
        return self.q("select * from candidate_briefs where agency_id = %s order by id", (agency_id,))

    def briefs_for_client(self, client_id: int):
        return self.q("select * from candidate_briefs where client_id = %s order by id", (client_id,))

    def save_brief(self, row: dict) -> dict:
        existing = self.one("select * from candidate_briefs where agency_id is not distinct from %s and "
                            "client_id is not distinct from %s and role_family_id = %s and lower(location) = lower(%s)",
                            (row.get("agency_id"), row.get("client_id"), row["role_family_id"], row["location"]))
        if existing:
            return self._update("candidate_briefs", existing["id"], row)
        return self._insert("candidate_briefs", row)

    def matches_for(self, brief_ids: list[int]):
        if not brief_ids:
            return []
        return self.q("select * from brief_matches where brief_id = any(%s) order by id", (list(brief_ids),))

    def upsert_match(self, row: dict) -> dict:
        return self.one(
            "insert into brief_matches (brief_id, employer_job_id, score, reasons, distance_miles) "
            "values (%(brief_id)s, %(employer_job_id)s, %(score)s, %(reasons)s, %(distance_miles)s) "
            "on conflict (brief_id, employer_job_id) do update set score = excluded.score, "
            "reasons = excluded.reasons, distance_miles = excluded.distance_miles returning *", row)

    def cache_get(self, key: str, max_age_days: float | None = None) -> Any:
        r = self.one("select value, fetched_at from http_cache where key = %s", (key,))
        if not r:
            return None
        if max_age_days is not None and dt.datetime.now(dt.timezone.utc) - r["fetched_at"] > dt.timedelta(days=max_age_days):
            return None
        return r["value"]

    def cache_set(self, key: str, value: Any) -> None:
        self.q("insert into http_cache (key, value, fetched_at) values (%s, %s, now()) on conflict (key) do update "
               "set value = excluded.value, fetched_at = now()", (key, Jsonb(value)))

    def hot_lead_keys(self, keys: list[str]) -> set[str]:
        if not keys:
            return set()
        return {r["lead_key"] for r in self.q("select lead_key from hot_leads where lead_key = any(%s) "
                                              "and status <> 'ignored'", (list(keys),))}

    # =========================================================================
    # Settings + tasks
    # =========================================================================
    def settings(self) -> dict[str, Any]:
        return {r["key"]: r["value"] for r in self.q("select key, value from app_settings")}

    def setting(self, key: str, default: Any = None) -> Any:
        r = self.one("select value from app_settings where key = %s", (key,))
        return r["value"] if r else default

    def set_setting(self, key: str, value: Any) -> None:
        self.q("insert into app_settings (key, value, updated_at) values (%s, %s, now()) on conflict (key) do update "
               "set value = excluded.value, updated_at = now()", (key, Jsonb(value)))

    def create_task(self, kind: str, ref_id: int | None = None) -> int:
        return self.val("insert into tasks (kind, ref_id) values (%s, %s) returning id", (kind, ref_id))

    def finish_task(self, task_id: int, status: str, result: Any = None, error: str | None = None) -> None:
        self.q("update tasks set status=%s, finished_at=now(), result=%s, error=%s where id=%s",
               (status, Jsonb(result) if result is not None else None, error, task_id))

    def get_task(self, task_id: int) -> dict | None:
        return self.one("select * from tasks where id = %s", (task_id,))

    # =========================================================================
    # Suppression
    # =========================================================================
    def suppress(self, email: str, reason: str = "") -> None:
        self.q("insert into suppression_list (email_hash, reason) values (%s, %s) on conflict do nothing",
               (email_hash(email), reason))

    def is_suppressed(self, email: str | None) -> bool:
        if not email:
            return False
        return bool(self.one("select 1 as x from suppression_list where email_hash = %s", (email_hash(email),)))

    def suppressed_hashes(self) -> set[str]:
        return {r["email_hash"] for r in self.q("select email_hash from suppression_list")}


def batched(items: Iterable[Any], n: int) -> Iterable[list[Any]]:
    buf: list[Any] = []
    for it in items:
        buf.append(it)
        if len(buf) >= n:
            yield buf
            buf = []
    if buf:
        yield buf


def as_json(v: Any) -> str:
    return json.dumps(v, default=_jsonable)
