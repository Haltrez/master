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

from .leads import CSV_HEADERS, LEAD_COLUMNS, lead_rows
from .pg import TerritoryTaken

log = logging.getLogger("orders")

# Each keyword is searched on every source, so a preset stays short (cost and time).
# Every word of a keyword must start a word in the job title (collect.title_matches).
INDUSTRIES: dict[str, list[str]] = {
    "Accountancy & Finance": ["accountant", "finance manager", "credit controller", "payroll", "financial controller"],
    "IT & Tech": ["software developer", "software engineer", "devops", "data analyst", "it support", "network engineer"],
    "Engineering & Manufacturing": ["mechanical engineer", "electrical engineer", "maintenance engineer",
                                    "quality engineer", "production manager", "cnc"],
    "Construction & Property": ["site manager", "quantity surveyor", "estimator", "contracts manager", "building surveyor"],
    "Sales & Marketing": ["sales executive", "sales manager", "sales consultant", "business development",
                          "account manager", "marketing manager",
                          "marketing executive"],
    "HR": ["hr advisor", "hr manager", "hr business partner", "hr administrator"],
    "Legal": ["solicitor", "paralegal", "legal secretary", "conveyancer"],
    "Healthcare & Care": ["nurse", "care assistant", "support worker", "healthcare assistant", "care manager"],
    "Logistics & Warehouse": ["warehouse operative", "hgv driver", "transport manager", "logistics", "forklift"],
    "Office & Admin": ["office manager", "administrator", "receptionist", "customer service"],
    "Hospitality": ["chef", "restaurant manager", "hotel manager", "bar manager"],
    "Education": ["teacher", "teaching assistant", "lecturer"],
}

SHEET_HEADERS = [CSV_HEADERS[c] for c in LEAD_COLUMNS] + ["Status"]
FREQUENCIES = ("once", "weekly")
PACKAGES = ("sheet", "outreach")


def keywords_for(industry: str, custom: list[str] | None = None) -> list[str]:
    kws = [k.strip().lower() for k in (custom or []) if k and k.strip()]
    if kws:
        return list(dict.fromkeys(kws))
    for name, preset in INDUSTRIES.items():
        if name.lower() == industry.strip().lower():
            return preset
    # Free-text industry with no preset: search for it as a job title.
    return [industry.strip().lower()]


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


def _fitting(store, order: dict, leads: list[dict], claude) -> list[dict]:
    """Only roles that belong to the order's industry (Claude Haiku, cached; keyword check without a key)."""
    from .fit import filter_fitting
    search = store.search_row(order["search_id"]) if order.get("search_id") else None
    keywords = list((search or {}).get("keywords") or keywords_for(order["industry"]))
    return filter_fitting(store, claude, order["industry"], keywords, leads)


def ready_count(store, order: dict, strict: bool = True) -> int:
    """Verified, on-industry leads found but not yet sent (no paid calls: known verdicts or the keyword check)."""
    if not order.get("client_id"):
        return 0
    leads = store.undelivered_leads(order["client_id"], 1000)
    if strict:
        ok = store.verified_employer_norms([l["employer_norm"] for l in leads])
        leads = [l for l in leads if l["employer_norm"] in ok]
    return len(_fitting(store, order, leads, None))


def order_lead_rows(store, order_id: int) -> list[dict[str, Any]]:
    """Every lead sent for an order, as sheet rows (for the CSV download / rebuilding a sheet)."""
    o = get_order(store, order_id)
    keys = [r["lead_key"] for r in store.q("select lead_key from deliveries where order_id = %s order by delivered_at, id",
                                           (order_id,))]
    if not keys:
        return []
    by_key = {l["lead_key"]: l for l in store.q("select * from hot_leads where lead_key = any(%s)", (keys,))}
    search = store.search_row(o["search_id"]) if o and o.get("search_id") else None
    return lead_rows(store, [by_key[k] for k in keys if k in by_key], dt.date.today(), search)


def sheet_row(r: dict[str, Any]) -> list[Any]:
    return [r.get(c, "") for c in LEAD_COLUMNS] + ["new"]


# ---------------------------------------------------------------------------
# creating
# ---------------------------------------------------------------------------
def create_order(store, *, agency_name: str, industry: str, location: str, leads_wanted: int,
                 frequency: str = "once", package: str = "sheet", client_email: str | None = None,
                 keywords: list[str] | None = None, radius_miles: int = 20) -> dict:
    agency_name, industry, location = agency_name.strip(), industry.strip(), location.strip()
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


# ---------------------------------------------------------------------------
# filling
# ---------------------------------------------------------------------------
def _set(store, order_id: int, **fields) -> None:
    store._update("lead_orders", order_id, fields)


def fill_order(store, settings, order_id: int, *, collect: Callable[[Any, Any, int], Any] | None = None,
               sheets=None, strict: bool = True, now: dt.datetime | None = None, claude=None) -> dict[str, Any]:
    """Collect (when `collect` is given), then send up to the number still owed. Safe to call any time."""
    now = now or _now()
    o = get_order(store, order_id)
    if o is None:
        raise ValueError(f"order {order_id} not found")
    if o["status"] == "paused":
        return {"order_id": order_id, "sent": 0, "status": "paused"}
    try:
        if collect is not None and o.get("search_id"):
            _set(store, order_id, status="collecting", note="Searching every job board...")
            collect(store, settings, o["search_id"])
        if o["frequency"] == "weekly" and now - o["period_start"] >= dt.timedelta(days=7):
            _set(store, order_id, period_start=now)
        o = get_order(store, order_id)
        owed = int(o["leads_wanted"]) - int(o["delivered_period"] if o["frequency"] == "weekly" else o["delivered_total"])
        sent = 0
        if owed > 0 and o.get("client_id"):
            leads = store.undelivered_leads(o["client_id"], 1000)
            if strict:
                ok = store.verified_employer_norms([l["employer_norm"] for l in leads])
                leads = [l for l in leads if l["employer_norm"] in ok]
            leads = _fitting(store, o, sorted(leads, key=lambda l: -l["score"])[:400], claude)[:owed]
            rows = lead_rows(store, leads, now.date())
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
                store.q("""insert into deliveries (client_id, lead_key, order_id)
                           select %s, k, %s from unnest(%s::text[]) k on conflict (client_id, lead_key) do nothing""",
                        (o["client_id"], order_id, [r["lead_key"] for r in rows]))
            sent = len(rows)
        o = get_order(store, order_id)
        have = int(o["delivered_period"] if o["frequency"] == "weekly" else o["delivered_total"])
        where = "" if sheets is not None else " Google isn't connected: download the CSV."
        if have >= o["leads_wanted"]:
            if o["frequency"] == "once":
                status, note = "done", f"All {have} leads sent.{where}"
            else:
                nxt = (o["period_start"] + dt.timedelta(days=7)).date()
                status, note = "waiting", f"{have} sent this week. Next batch from {nxt:%a %d %b}.{where}"
        else:
            status = "waiting"
            note = (f"{have} of {o['leads_wanted']} sent. More are added every morning as new reposts are found "
                    f"and companies are checked.{where}")
        _set(store, order_id, status=status, note=note, last_run_at=now)
        return {"order_id": order_id, "sent": sent, "status": status, "note": note, "sheet_url": o.get("sheet_url")}
    except Exception as exc:
        log.exception("order %d failed", order_id)
        _set(store, order_id, status="error", note=str(exc)[:500] or type(exc).__name__, last_run_at=now)
        return {"order_id": order_id, "sent": 0, "status": "error", "note": str(exc)}


def top_up_all(store, settings, sheets=None, strict: bool = True, claude=None) -> list[dict]:
    """After the daily collection: send what's owed on every open order (no extra collection)."""
    out = []
    for o in store.q("select id from lead_orders where status in ('collecting', 'waiting', 'error') order by id"):
        out.append(fill_order(store, settings, o["id"], sheets=sheets, strict=strict, claude=claude))
    return out
