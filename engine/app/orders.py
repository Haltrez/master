"""Lead orders: the one thing the owner does every day.

"Make 20 hot leads for Acme Recruitment, IT, London, and send them."
create_order() sets up the agency, the client, an exclusive search
(industry + region) and the order. fill_order() collects, then delivers up
to the number asked for into the order's Google Sheet (or CSV when Google
isn't connected). One-off orders top up every day until they reach their
number; weekly orders get that many new leads each week.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any, Callable

from .pg import TerritoryTaken

log = logging.getLogger("orders")

from . import industries as IND
from .leadgen import ORDER_COLUMNS, Tools, candidates, pick

INDUSTRIES = {k: v["search"] for k, v in IND.INDUSTRIES.items()}   # name -> job titles searched
SHEET_HEADERS = ORDER_COLUMNS
FREQUENCIES = ("once", "daily", "weekly")
PACKAGES = ("sheet", "outreach")


def keywords_for(industry: str, custom: list[str] | None = None) -> list[str]:
    kws = [k.strip().lower() for k in (custom or []) if k and k.strip()]
    if kws:
        return list(dict.fromkeys(kws))
    return list(IND.definition(industry)["search"])


def canonical_industry(industry: str) -> str:
    """"Sales" -> "Sales & Marketing", "M&E" -> "Building Services (M&E)"; anything else as typed."""
    d = IND.get(industry or "")
    return d["name"] if d else " ".join((industry or "").split())


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


# ---------------------------------------------------------------------------
# reading
# ---------------------------------------------------------------------------
ORDER_SQL = """
    select o.*, a.name as agency_name,
      (select count(*) from deliveries d where d.order_id = o.id) as delivered_total,
      (select count(*) from deliveries d where d.order_id = o.id and d.delivered_at >= o.period_start) as delivered_period
    from lead_orders o left join agencies a on a.id = o.agency_id"""


def get_order(store, order_id: int) -> dict | None:
    return store.one(ORDER_SQL + " where o.id = %s", (order_id,))


def list_orders(store, status: str | None = None) -> list[dict]:
    if status:
        return store.q(ORDER_SQL + " where o.status = %s order by o.id desc", (status,))
    return store.q(ORDER_SQL + " order by o.id desc")


def ready_count(store, order: dict, strict: bool = True) -> int:
    """Companies in this territory still to be checked/sent (an upper bound: Haiku and Companies House decide)."""
    if not order.get("client_id") or not order.get("search_id"):
        return 0
    return len({l["employer_norm"] for l in candidates(store, order, _now())})


def order_lead_rows(store, order_id: int) -> list[dict[str, Any]]:
    """Every row sent for an order, exactly as it went into the sheet."""
    return [r["row"] for r in store.q("select row from deliveries where order_id = %s and row is not null "
                                      "order by delivered_at, id", (order_id,))]


def sheet_row(r: dict[str, Any]) -> list[Any]:
    return [r.get(c, "") for c in ORDER_COLUMNS]


# ---------------------------------------------------------------------------
# creating
# ---------------------------------------------------------------------------
def create_order(store, *, agency_name: str, industry: str, location: str, leads_wanted: int,
                 frequency: str = "once", package: str = "sheet", client_email: str | None = None,
                 keywords: list[str] | None = None, radius_miles: int = 20) -> dict:
    agency_name, industry, location = agency_name.strip(), canonical_industry(industry), location.strip()
    if not (agency_name and industry and location):
        raise ValueError("agency, industry and location are all needed")
    if frequency not in FREQUENCIES:
        raise ValueError(f"frequency must be one of {FREQUENCIES}")
    if package not in PACKAGES:
        raise ValueError(f"package must be one of {PACKAGES}")
    if not 1 <= int(leads_wanted) <= 500:
        raise ValueError("leads_wanted must be between 1 and 500")
    kws = keywords_for(industry, keywords)
    label = f"{industry} - {location}"

    with store.conn.transaction():
        agency = store.agency_by_name(agency_name) or store.create_agency(agency_name)
        search = store.one("select * from searches where lower(label) = lower(%s) order by id limit 1", (label,))
        owner = None
        if search:
            owner = store.one("""select c.* from client_territories t join clients c on c.id = t.client_id
                                 where t.search_id = %s""", (search["id"],))
            if owner and owner["agency_id"] != agency["id"]:
                other = store.one("select name from agencies where id = %s", (owner["agency_id"],))
                raise TerritoryTaken(f"{label} is already exclusive to {other['name'] if other else 'another agency'}")
            store.q("update searches set keywords = %s, radius_miles = %s, active = true where id = %s",
                    (kws, radius_miles, search["id"]))
        else:
            search = store.one("""insert into searches (label, keywords, location, radius_miles)
                                  values (%s, %s, %s, %s) returning *""", (label, kws, location, radius_miles))
        if owner:
            client_id = owner["id"]
            if client_email and not owner.get("delivery_email"):
                store.q("update clients set delivery_email = %s where id = %s", (client_email, client_id))
        else:
            client = store.one("""insert into clients (agency_id, plan, start_date, delivery_email)
                                  values (%s, %s, current_date, %s) returning *""",
                               (agency["id"], "done_for_you" if package == "outreach" else "pilot", client_email))
            client_id = client["id"]
            store.q("insert into client_territories (client_id, search_id) values (%s, %s)", (client_id, search["id"]))
            store.q("update agencies set stage = 'paid' where id = %s and stage not in ('paid')", (agency["id"],))
        order = store.one("""insert into lead_orders (agency_id, client_id, search_id, industry, location, radius_miles,
                               leads_wanted, frequency, package, client_email, status, note)
                             values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'collecting', 'Searching every job board...')
                             returning id""",
                          (agency["id"], client_id, search["id"], industry, location, radius_miles, int(leads_wanted),
                           frequency, package, client_email))
    return get_order(store, order["id"])


def update_order(store, order_id: int, fields: dict) -> dict | None:
    allowed = {k: v for k, v in fields.items() if k in ("leads_wanted", "frequency", "client_email", "status", "package")}
    if "status" in allowed and allowed["status"] not in ("paused", "waiting"):
        raise ValueError("status can only be set to paused or waiting (resume)")
    if allowed:
        store._update("lead_orders", order_id, allowed)
    return get_order(store, order_id)


def delete_order(store, order_id: int) -> bool:
    """Remove an order and what it sent. When the agency's client has no other order, the client goes too,
    which frees its industry + region for another agency. Hot leads themselves are kept.
    (A Google Sheet already made stays in the owner's Drive.)"""
    o = get_order(store, order_id)
    if o is None:
        return False
    with store.conn.transaction():
        store.q("delete from deliveries where order_id = %s", (order_id,))
        store.q("delete from lead_orders where id = %s", (order_id,))
        cid = o.get("client_id")
        if cid and not store.val("select count(*) from lead_orders where client_id = %s", (cid,)):
            sids = [r["search_id"] for r in store.q("select search_id from client_territories where client_id = %s", (cid,))]
            store.q("delete from clients where id = %s", (cid,))   # territories + deliveries cascade
            store.q("update searches set active = false where id = any(%s)", (sids,))
    return True


# ---------------------------------------------------------------------------
# filling
# ---------------------------------------------------------------------------
def _set(store, order_id: int, **fields) -> None:
    store._update("lead_orders", order_id, fields)


def _new_period(o: dict, now: dt.datetime) -> bool:
    """Daily orders start a new batch each UK morning; weekly ones every 7 days."""
    if o["frequency"] == "weekly":
        return now - o["period_start"] >= dt.timedelta(days=7)
    if o["frequency"] == "daily":
        from zoneinfo import ZoneInfo
        uk = ZoneInfo("Europe/London")
        return now.astimezone(uk).date() > o["period_start"].astimezone(uk).date()
    return False


def fill_order(store, settings, order_id: int, *, collect: Callable[[Any, Any, int], Any] | None = None,
               sheets=None, strict: bool = True, now: dt.datetime | None = None, claude=None,
               tools: Tools | None = None) -> dict[str, Any]:
    """Collect (when `collect` is given), then send up to the number still owed. Safe to call any time."""
    now = now or _now()
    tools = tools or Tools(claude=claude)
    o = get_order(store, order_id)
    if o is None:
        raise ValueError(f"order {order_id} not found")
    if o["status"] == "paused":
        return {"order_id": order_id, "sent": 0, "status": "paused"}
    try:
        if collect is not None and o.get("search_id"):
            _set(store, order_id, status="collecting", note="Searching every job board...")
            collect(store, settings, o["search_id"])
        if _new_period(o, now):
            _set(store, order_id, period_start=now)
        o = get_order(store, order_id)
        owed = int(o["leads_wanted"]) - int(o["delivered_period"] if o["frequency"] != "once" else o["delivered_total"])
        sent, stats = 0, {}
        if owed > 0 and o.get("client_id"):
            _set(store, order_id, note="Checking each ad with Claude and Companies House...")
            rows, stats = pick(store, o, tools, owed, strict=strict, now=now)
            if sheets is not None:
                if not o.get("sheet_id"):
                    title = f"{o['agency_name']} - {o['industry']} {o['location']} hot leads"
                    made = sheets.create(title, SHEET_HEADERS)
                    _set(store, order_id, sheet_id=made["sheet_id"], sheet_url=made["sheet_url"])
                    o.update(made)
                    earlier = order_lead_rows(store, order_id)   # sent before Google was connected
                    sheets.append(made["sheet_id"], [sheet_row(r) for r in earlier])
                    if o.get("client_email"):
                        try:
                            sheets.share(made["sheet_id"], o["client_email"])
                        except Exception as exc:   # the sheet still works; the owner can share it by hand
                            log.warning("order %d: sharing failed: %s", order_id, exc)
                sheets.append(o["sheet_id"], [sheet_row(r) for r in rows])   # before marking, so a failure retries
            if rows:
                from psycopg.types.json import Jsonb
                for r in rows:
                    store.q("""insert into deliveries (client_id, lead_key, order_id, row) values (%s, %s, %s, %s)
                               on conflict (client_id, lead_key) do nothing""",
                            (o["client_id"], r["lead_key"], order_id, Jsonb({k: v for k, v in r.items() if k != "lead_key"})))
            sent = len(rows)
        o = get_order(store, order_id)
        have = int(o["delivered_period"] if o["frequency"] != "once" else o["delivered_total"])
        where = "" if sheets is not None else " Google isn't connected: download the CSV."
        if have >= o["leads_wanted"]:
            if o["frequency"] == "once":
                status, note = "done", f"All {have} leads sent.{where}"
            elif o["frequency"] == "daily":
                status, note = "waiting", f"{have} sent today. More tomorrow morning.{where}"
            else:
                nxt = (o["period_start"] + dt.timedelta(days=7)).date()
                status, note = "waiting", f"{have} sent this week. Next batch from {nxt:%a %d %b}.{where}"
        else:
            status = "waiting"
            checked = ""
            if stats.get("in_crm"):
                checked += f" {stats['in_crm']} left out: already in their CRM."
            if stats.get("judged"):
                checked += (f" Checked {stats['judged']} reposted ads: {stats['failed_haiku']} were the wrong kind of job"
                           f" and {stats['failed_company_check']} failed the company check.")
            period = {"daily": " today", "weekly": " this week"}.get(o["frequency"], "")
            note = (f"{have} of {o['leads_wanted']} sent{period}.{checked} New reposts are checked every morning.{where}")
        _set(store, order_id, status=status, note=note, last_run_at=now)
        return {"order_id": order_id, "sent": sent, "status": status, "note": note, "sheet_url": o.get("sheet_url"),
                "stats": stats}
    except Exception as exc:
        log.exception("order %d failed", order_id)
        _set(store, order_id, status="error", note=str(exc)[:500] or type(exc).__name__, last_run_at=now)
        return {"order_id": order_id, "sent": 0, "status": "error", "note": str(exc)}


def top_up_all(store, settings, sheets=None, strict: bool = True, claude=None, tools: Tools | None = None) -> list[dict]:
    """After the daily collection: send what's owed on every open order (no extra collection)."""
    out = []
    for o in store.q("select id from lead_orders where status in ('collecting', 'waiting', 'error') order by id"):
        out.append(fill_order(store, settings, o["id"], sheets=sheets, strict=strict, claude=claude, tools=tools))
    return out


# ---------------------------------------------------------------------------
# territories collected ahead of a sale
# ---------------------------------------------------------------------------
def watch_territory(store, industry: str, location: str, radius_miles: int = 20,
                    keywords: list[str] | None = None) -> dict:
    """Start collecting an industry + town every morning before any agency buys it, so reposts are
    already visible on the day an order comes in (an order for it reuses the same history)."""
    industry, location = canonical_industry(industry), location.strip()
    if not (industry and location):
        raise ValueError("industry and location are both needed")
    label = f"{industry} - {location}"
    s = store.one("select * from searches where lower(label) = lower(%s) order by id limit 1", (label,))
    if s:
        store.q("update searches set active = true where id = %s", (s["id"],))
        return territory(store, s["id"])
    s = store.one("insert into searches (label, keywords, location, radius_miles) values (%s, %s, %s, %s) returning id",
                  (label, keywords_for(industry, keywords), location, radius_miles))
    return territory(store, s["id"])


TERRITORY_SQL = """
    select s.id as search_id, s.label, s.location, s.radius_miles, s.active, s.last_run_at,
      split_part(s.label, ' - ', 1) as industry,
      (select a.name from client_territories t join clients c on c.id = t.client_id
         join agencies a on a.id = c.agency_id where t.search_id = s.id limit 1) as owner,
      (select count(*) from listing_searches ls join listings l on l.job_id = ls.job_id
         where ls.search_id = s.id and l.posted_by = 'employer' and l.last_seen_at > now() - interval '14 days') as ads,
      (select count(*) from hot_lead_searches hs join hot_leads h on h.lead_key = hs.lead_key
         where hs.search_id = s.id and (h.posting_count_90d >= 2 or h.flagged_repost or h.archive_prior > 0)) as reposted_roles,
      (select min(l.first_seen_at)::date from listing_searches ls join listings l on l.job_id = ls.job_id
         where ls.search_id = s.id) as collecting_since
    from searches s where position(' - ' in s.label) > 0"""


def territory(store, search_id: int) -> dict | None:
    return store.one(TERRITORY_SQL + " and s.id = %s", (search_id,))


def territories(store) -> list[dict]:
    return store.q(TERRITORY_SQL + " order by s.active desc, s.label")


def preview_territory(store, search_id: int, tools: Tools, limit: int = 50) -> dict:
    """How many leads would pass every check today, without sending anything (Haiku + Companies House only)."""
    t = territory(store, search_id)
    if t is None:
        raise ValueError("territory not found")
    fake = {"id": f"preview:{search_id}", "client_id": 0, "search_id": search_id, "industry": t["industry"],
            "location": t["location"]}
    quiet = Tools(claude=tools.claude, ch=tools.ch, reed=tools.reed)   # no Brave/Hunter spend on a preview
    rows, stats = pick(store, fake, quiet, limit, strict=True)
    keep = ("Company", "Role", "Also hiring", "Times posted", "Location", "Salary", "Ad link", "Why it's a lead")
    stats["not_current"] = max(0, int(t["reposted_roles"]) - stats["candidates"])   # last ad over 10 days ago
    return {"territory": t["label"], "reposted_roles": t["reposted_roles"], "ready": len(rows), "checked": stats,
            "leads": [{k: r[k] for k in keep} for r in rows],
            "sample": [{k: r[k] for k in ("Company", "Role", "Times posted", "Why it's a lead")} for r in rows[:10]]}


# ---------------------------------------------------------------------------
# leads sent + agencies (the CRM's Leads and Agencies pages)
# ---------------------------------------------------------------------------
def sent_leads(store, order_id: int | None = None) -> list[dict]:
    where, params = "d.row is not null", []
    if order_id:
        where += " and d.order_id = %s"; params.append(order_id)
    return store.q(f"""select d.id, d.order_id, d.outcome, d.delivered_at, d.row, a.name as agency_name,
                              o.industry, o.location
                       from deliveries d join lead_orders o on o.id = d.order_id left join agencies a on a.id = o.agency_id
                       where {where} order by d.delivered_at desc, d.id desc limit 2000""", params)


def agencies_overview(store) -> dict:
    clients = store.q("""
        select a.id, a.name, a.website, a.crm_match_mode,
          (select count(*) from agency_crm_companies k where k.agency_id = a.id) as crm_companies,
          (select max(k.uploaded_at) from agency_crm_companies k where k.agency_id = a.id) as crm_uploaded_at,
          (select max(o.client_email) from lead_orders o where o.agency_id = a.id) as email,
          coalesce((select json_agg(json_build_object('id', o.id, 'industry', o.industry, 'location', o.location,
                     'status', o.status, 'leads_wanted', o.leads_wanted, 'frequency', o.frequency, 'package', o.package,
                     'sent', (select count(*) from deliveries d where d.order_id = o.id)) order by o.id)
                    from lead_orders o where o.agency_id = a.id), '[]') as orders,
          (select count(*) from deliveries d join lead_orders o on o.id = d.order_id where o.agency_id = a.id) as leads_sent,
          (select count(*) from deliveries d join lead_orders o on o.id = d.order_id
             where o.agency_id = a.id and d.outcome = 'contacted') as contacted,
          (select count(*) from deliveries d join lead_orders o on o.id = d.order_id
             where o.agency_id = a.id and d.outcome = 'meeting') as meetings,
          (select count(*) from deliveries d join lead_orders o on o.id = d.order_id
             where o.agency_id = a.id and d.outcome = 'placement') as placements
        from agencies a where exists (select 1 from lead_orders o where o.agency_id = a.id) order by a.name""")
    prospects = store.q("""
        select a.id, a.name, a.website, a.ads_count_90d as ads, a.ch_company_type, a.ch_company_status,
          (select string_agg(distinct s.label, ', ') from agency_searches x join searches s on s.id = x.search_id
             where x.agency_id = a.id and s.active) as territories,
          (select c.full_name from contacts c where c.agency_id = a.id and not c.do_not_contact
             order by c.email_verified desc, (c.source = 'companies_house') desc, c.id limit 1) as contact,
          (select c.email from contacts c where c.agency_id = a.id and c.email_verified and not c.do_not_contact
             order by c.id limit 1) as email
        from agencies a
        where not exists (select 1 from lead_orders o where o.agency_id = a.id) and a.stage <> 'do_not_contact'
          and exists (select 1 from agency_searches x join searches s on s.id = x.search_id
                      where x.agency_id = a.id and s.active)
        order by a.ads_count_90d desc, a.name limit 300""")
    return {"clients": clients, "prospects": prospects}
