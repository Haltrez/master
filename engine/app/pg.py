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


class _CoreStore:
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


# =============================================================================
# Phase 2: detection queries (mixed into PgStore below)
# =============================================================================
class _DetectMixin:
    def employer_listings_since(self, since: dt.datetime) -> list[dict]:
        return self.q("""
            select l.job_id, l.source, l.employer_name, l.employer_norm, l.title, l.title_norm,
                   l.location_name, coalesce(l.location_norm, '') as location_norm, l.first_seen_at,
                   l.last_seen_at, l.posted_date, l.expiration_date, l.url,
                   coalesce(array_agg(ls.search_id) filter (where ls.search_id is not null), '{}') as search_ids
            from listings l left join listing_searches ls on ls.job_id = l.job_id
            where l.posted_by = 'employer' and l.last_seen_at >= %s
            group by l.job_id""", (since,))

    def latest_run_starts(self) -> dict[int, dt.datetime]:
        return {r["search_id"]: r["started"] for r in self.q(
            "select search_id, max(started_at) as started from runs where status <> 'error' "
            "and search_id is not null group by search_id")}

    def hot_lead_status_map(self) -> dict[str, str]:
        return {r["lead_key"]: r["status"] for r in self.q("select lead_key, status from hot_leads")}

    def save_hot_leads(self, leads: list[dict]) -> None:
        cols = ("lead_key", "employer_name", "employer_norm", "title", "title_norm", "location_name",
                "location_norm", "posting_count_90d", "first_seen_at", "last_seen_at", "live_now", "long_open",
                "score", "latest_url", "job_ids", "expiration_date", "sources")
        sql = (f"insert into hot_leads ({', '.join(cols)}, updated_at) values ({', '.join(['%s'] * len(cols))}, now()) "
               "on conflict (lead_key) do update set "
               + ", ".join(f"{c} = excluded.{c}" for c in cols if c not in ("lead_key", "latest_url"))
               + ", latest_url = coalesce(excluded.latest_url, hot_leads.latest_url), updated_at = now()")
        with self.conn.transaction():
            with self.conn.cursor() as cur:
                for lead in leads:
                    cur.execute(sql, [lead[c] for c in cols])
                keys = [l["lead_key"] for l in leads]
                cur.execute("delete from hot_lead_searches where lead_key = any(%s)", (keys,))
                for lead in leads:
                    for sid in lead.get("search_ids", []):
                        cur.execute("insert into hot_lead_searches (lead_key, search_id) values (%s, %s) "
                                    "on conflict do nothing", (lead["lead_key"], sid))

    def prune_hot_leads(self, keep: set[str]) -> int:
        """Leads that cooled off: drop untouched ones; keep anything the owner or a client used."""
        rows = self.q("""
            delete from hot_leads h where h.status = 'new' and not (h.lead_key = any(%s))
              and not exists (select 1 from deliveries d where d.lead_key = h.lead_key)
            returning lead_key""", (list(keep),))
        self.q("update hot_leads set live_now = false where not (lead_key = any(%s))", (list(keep),))
        return len(rows)

    def known_agency_norms(self) -> set[str]:
        """Employers Reed told us are agencies, plus manual overrides."""
        rows = self.q("select distinct employer_norm from listings where posted_by = 'agency' and source = 'reed' "
                      "union select employer_norm from employer_overrides where is_agency")
        return {r["employer_norm"] for r in rows}

    def hot_leads(self, *, search_id: int | None = None, min_score: int | None = None,
                  since: dt.datetime | None = None, status: str | None = None, live_only: bool = False,
                  limit: int = 500) -> list[dict]:
        where, params = ["true"], []
        if search_id:
            where.append("exists (select 1 from hot_lead_searches s where s.lead_key = h.lead_key and s.search_id = %s)")
            params.append(search_id)
        if min_score is not None:
            where.append("h.score >= %s"); params.append(min_score)
        if since:
            where.append("h.updated_at >= %s"); params.append(since)
        if status:
            where.append("h.status = %s"); params.append(status)
        if live_only:
            where.append("h.live_now")
        params.append(limit)
        return self.q(f"""select h.*, coalesce((select array_agg(search_id) from hot_lead_searches s
                                                where s.lead_key = h.lead_key), '{{}}') as search_ids
                          from hot_leads h where {' and '.join(where)}
                          order by h.score desc, h.last_seen_at desc limit %s""", params)


# =============================================================================
# Phase 3: agencies and contacts
# =============================================================================
OWNER_AGENCY_FIELDS = {"stage", "notes", "website", "next_follow_up", "name"}
AGENCY_STAGES = ("new", "researching", "contacted", "replied", "call_booked", "pilot", "paid", "lost",
                 "do_not_contact")


class _AgencyMixin:
    def rebuild_agencies_sql(self, since: dt.datetime) -> dict[str, int]:
        with self.conn.transaction():
            before = self.val("select count(*) from agencies")
            self.q("""
                with ads as (
                  select employer_norm, mode() within group (order by employer_name) as name,
                         source, count(distinct job_id) as n
                  from listings where posted_by = 'agency' and last_seen_at >= %s and employer_norm <> ''
                  group by employer_norm, source
                ), per as (
                  select employer_norm, max(name) as name, max(n) as n from ads group by employer_norm
                )
                insert into agencies (name, name_norm, ads_count_90d)
                select name, employer_norm, n from per
                on conflict (name_norm) do update set ads_count_90d = excluded.ads_count_90d""", (since,))
            self.q("""update agencies a set ads_count_90d = 0 where not exists (
                        select 1 from listings l where l.posted_by = 'agency' and l.employer_norm = a.name_norm
                        and l.last_seen_at >= %s)""", (since,))
            self.q("delete from agency_searches")
            self.q("""
                insert into agency_searches (agency_id, search_id, ads_count)
                select agency_id, search_id, max(n) from (
                  select a.id as agency_id, ls.search_id, l.source, count(distinct l.job_id) as n
                  from listings l join listing_searches ls on ls.job_id = l.job_id
                  join agencies a on a.name_norm = l.employer_norm
                  where l.posted_by = 'agency' and l.last_seen_at >= %s
                  group by a.id, ls.search_id, l.source) per_source
                group by agency_id, search_id""", (since,))
            total = self.val("select count(*) from agencies where ads_count_90d > 0")
            after = self.val("select count(*) from agencies")
        return {"agencies": total, "new": after - before}

    def agencies_list(self, *, stage: str | None = None, search_id: int | None = None, q: str | None = None,
                      limit: int = 1000) -> list[dict]:
        where, params = ["true"], []
        if stage:
            where.append("a.stage = %s"); params.append(stage)
        if search_id:
            where.append("exists (select 1 from agency_searches s where s.agency_id = a.id and s.search_id = %s)")
            params.append(search_id)
        if q:
            where.append("a.name ilike %s"); params.append(f"%{q}%")
        params.append(limit)
        return self.q(f"""
            select a.*,
              (select c.full_name from contacts c where c.agency_id = a.id and not c.do_not_contact
                 order by (c.source = 'companies_house') desc, c.id limit 1) as director_name,
              (select string_agg(distinct s.label, ', ') from agency_searches x join searches s on s.id = x.search_id
                 where x.agency_id = a.id) as niches,
              (select count(*) from contacts c where c.agency_id = a.id and c.email_verified) as verified_emails,
              (select max(happened_at) from activities t where t.agency_id = a.id) as last_activity_at
            from agencies a where {' and '.join(where)}
            order by a.ads_count_90d desc, a.name limit %s""", params)

    def get_agency(self, agency_id: int) -> dict | None:
        return self.one("select * from agencies where id = %s", (agency_id,))

    def update_agency(self, agency_id: int, fields: dict) -> dict | None:
        return self._update("agencies", agency_id, fields)

    def contacts(self, agency_id: int) -> list[dict]:
        return self.q("select * from contacts where agency_id = %s order by (source = 'companies_house') desc, id",
                      (agency_id,))

    def contact(self, contact_id: int) -> dict | None:
        return self.one("select * from contacts where id = %s", (contact_id,))

    def add_contact(self, row: dict) -> dict | None:
        """Insert unless the same person is already on the agency. Returns the new row or None."""
        exists = self.one("select id from contacts where agency_id = %s and lower(full_name) = lower(%s)",
                          (row["agency_id"], row["full_name"]))
        if exists:
            return None
        if row.get("email") and self.is_suppressed(row["email"]):
            row = {**row, "email": None, "email_verified": False}
        return self._insert("contacts", row)

    def update_contact(self, contact_id: int, fields: dict) -> dict | None:
        if fields.get("email") and self.is_suppressed(fields["email"]):
            raise ValueError("this email is on the suppression list")
        return self._update("contacts", contact_id, fields)

    def agency_live_ads(self, agency_id: int, days: int = 14, limit: int = 50) -> list[dict]:
        return self.q("""select l.job_id, l.title, l.location_name, l.url, l.first_seen_at, l.last_seen_at, l.source
                         from listings l join agencies a on a.name_norm = l.employer_norm
                         where a.id = %s and l.posted_by = 'agency' and l.last_seen_at >= now() - make_interval(days => %s)
                         order by l.last_seen_at desc limit %s""", (agency_id, days, limit))

    def agency_search_ids(self, agency_id: int) -> list[int]:
        return [r["search_id"] for r in self.q("select search_id from agency_searches where agency_id = %s "
                                               "order by ads_count desc", (agency_id,))]

    def agencies_to_enrich(self, limit: int = 50) -> list[dict]:
        return self.q("select * from agencies where ch_checked_at is null and stage <> 'do_not_contact' "
                      "order by ads_count_90d desc limit %s", (limit,))


# =============================================================================
# The store: every mixin above. New query groups are added as mixins.
# =============================================================================
class PgStore(_CoreStore, _DetectMixin, _AgencyMixin):
    pass
