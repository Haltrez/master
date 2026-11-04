"""From hot leads to rows in an agency's sheet. Every row passes all of these, in this order:

 1. Repost evidence   the role was advertised 2+ separate times (copies of one ad on the same day count
                      once), or a board marks it as a repost, or old ads are in the web archive.
 2. Still hiring      its newest ad was seen in the last 10 days.
 3. Not sent before   this agency hasn't had this company yet (one row per company).
 4. Claude Haiku      reads the newest ad's full text: in the industry, direct employer, real vacancy,
                      something an agency could fill. Four yeses or it's out.
 5. Companies House   an active UK company and not a recruitment business (when "real companies only").
 6. Decision maker    directors from Companies House (free), then Hunter if allowed this month.

Rows carry the evidence ("Why it's a lead") so the agency can trust each one.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass
from typing import Any

from . import industries as IND
from .collect import plain_text
from .normalise import location_norm
from .qualify import Verdict, judge

log = logging.getLogger("leadgen")

ORDER_COLUMNS = ["Date added", "Company", "Role", "Also hiring", "Salary", "Times posted", "Posted on", "Location",
                 "Ad link", "Previous ad", "Why it's a lead", "Decision maker", "Their role", "Email",
                 "Suggested email", "Status"]
STILL_HIRING_DAYS = 10
CHUNK = 24
MAX_JUDGED_PER_FILL = 400


@dataclass
class Tools:
    claude: Any = None
    ch: Any = None
    reed: Any = None
    brave: Any = None
    hunter: Any = None


# ---------------------------------------------------------------------------
# formatting
# ---------------------------------------------------------------------------
def money(lo: Any, hi: Any) -> str:
    vals = [float(v) for v in (lo, hi) if v not in (None, "", 0)]
    if not vals:
        return ""
    lo_v, hi_v = min(vals), max(vals)
    if hi_v < 200:
        unit, fmt = "/hr", (lambda v: f"£{v:,.2f}".replace(".00", ""))
    elif hi_v < 1500:
        unit, fmt = "/day", (lambda v: f"£{v:,.0f}")
    else:
        unit, fmt = "", (lambda v: f"£{v:,.0f}")
    return (fmt(hi_v) if lo_v == hi_v else f"{fmt(lo_v)} - {fmt(hi_v)}") + unit


def place(name: str | None) -> str:
    """'OL10 1PN' -> 'Oldham'; anything else as it is."""
    if not name:
        return ""
    n = location_norm(name)
    stripped = name.replace(" ", "")
    if n and len(stripped) <= 8 and any(ch.isdigit() for ch in stripped):
        return n.title()
    return name


def month(d: dt.date) -> str:
    return d.strftime("%b %Y")


def _as_date(v: Any) -> dt.date | None:
    if isinstance(v, dt.datetime):
        return v.date()
    if isinstance(v, dt.date):
        return v
    if isinstance(v, str) and v:
        return dt.date.fromisoformat(v[:10])
    return None


def niche_words(industry: str) -> str:
    return industry.replace("&", "and").lower()


# ---------------------------------------------------------------------------
# data
# ---------------------------------------------------------------------------
def _ads(store, job_ids: list[int]) -> dict[int, dict]:
    if not job_ids:
        return {}
    return {r["job_id"]: r for r in store.q(
        """select job_id, source, title, employer_name, location_name, min_salary, max_salary, url, posted_date,
                  first_seen_at, last_seen_at, description from listings where job_id = any(%s)""", (job_ids,))}


def _newest(ads: list[dict]) -> dict:
    return max(ads, key=lambda a: (_as_date(a["posted_date"]) or a["first_seen_at"].date(), a["first_seen_at"], a["job_id"]))


def _full_text(store, tools: Tools, ad: dict) -> str:
    """Search results only carry a snippet; Reed's details call has the whole ad (cached per job)."""
    text = ad.get("description") or ""
    if ad["source"] == "reed" and len(text) < 700 and tools.reed is not None:
        key = f"reed_details:{ad['job_id']}"
        cached = store.cache_get(key, max_age_days=30)
        if cached is None:
            try:
                d = tools.reed.get_details(ad["job_id"])
                cached = {"text": plain_text(d.get("jobDescription"), 6000) or "", "salary": d.get("salary") or ""}
                store.cache_set(key, cached)
            except Exception as exc:
                log.info("reed details for %s skipped: %s", ad["job_id"], exc)
                cached = None
        if cached:
            text = cached["text"] or text
            ad["salary_text"] = cached.get("salary") or ""
    return text


def candidates(store, order: dict, now: dt.datetime) -> list[dict]:
    """Hot leads in the order's territory that could still go out (steps 1-3)."""
    rows = store.q(
        """select h.* from hot_leads h join hot_lead_searches hs on hs.lead_key = h.lead_key
           where hs.search_id = %s and h.status <> 'ignored'
             and (h.posting_count_90d >= 2 or h.flagged_repost or h.archive_prior > 0)
             and h.last_seen_at >= %s
             and not exists (select 1 from deliveries d join hot_leads x on x.lead_key = d.lead_key
                             where d.client_id = %s and x.employer_norm = h.employer_norm)
           order by h.score desc, h.last_seen_at desc""",
        (order["search_id"], now - dt.timedelta(days=STILL_HIRING_DAYS), order["client_id"]))
    seen, out = set(), []
    for r in rows:
        if r["lead_key"] not in seen:
            seen.add(r["lead_key"])
            out.append(r)
    return out


# ---------------------------------------------------------------------------
# the pipeline
# ---------------------------------------------------------------------------
def pick(store, order: dict, tools: Tools, owed: int, *, strict: bool = True,
         now: dt.datetime | None = None) -> tuple[list[dict], dict[str, int]]:
    """Up to `owed` rows (one per company), best first, plus counts of what was dropped and why."""
    from .lead_contacts import enrich_lead_company, verify_lead_company
    now = now or dt.datetime.now(dt.timezone.utc)
    search = store.search_row(order["search_id"]) or {}
    industry = IND.definition(order["industry"], list(search.get("keywords") or []))
    stats = {"candidates": 0, "judged": 0, "not_judged": 0, "failed_haiku": 0, "failed_company_check": 0}
    cands = candidates(store, order, now)
    stats["candidates"] = len(cands)
    good: dict[str, list[tuple[dict, Verdict, dict]]] = {}     # employer_norm -> passing roles
    bad_company: set[str] = set()
    contacts: dict[str, dict] = {}

    def company_ok(lead: dict) -> bool:
        norm = lead["employer_norm"]
        if norm in bad_company:
            return False
        if norm in good or not strict:
            return True
        c = contacts.get(norm) or store.lead_contacts_for([norm]).get(norm)
        if (c is None or c.get("ch_company_type") is None) and tools.ch is not None:
            try:
                c = verify_lead_company(store, lead["employer_name"], norm, tools.ch)
            except Exception as exc:
                log.warning("companies house check for %s failed: %s", lead["employer_name"], exc)
                return False   # not cached as bad: tried again next time
        if c:
            contacts[norm] = c
        if not (c and c.get("verified_company")):
            bad_company.add(norm)
            stats["failed_company_check"] += 1
            return False
        return True

    judged = 0
    for i in range(0, len(cands), CHUNK):
        if len(good) >= owed or judged >= MAX_JUDGED_PER_FILL:
            break
        chunk = [l for l in cands[i:i + CHUNK] if l["employer_norm"] not in bad_company]
        all_ads = _ads(store, [j for l in chunk for j in (l.get("job_ids") or [])])
        items = []
        for l in chunk:
            ads = [all_ads[j] for j in (l.get("job_ids") or []) if j in all_ads]
            if ads:
                ad = dict(_newest(ads))
                ad["text"] = _full_text(store, tools, ad)
                items.append((l, ad))
        verdicts = judge(store, tools.claude, industry, [
            {"id": f"{l['lead_key']}:{ad['job_id']}", "company": l["employer_name"], "title": ad["title"],
             "location": place(ad["location_name"]),
             "salary": ad.get("salary_text") or money(ad["min_salary"], ad["max_salary"]),
             "description": ad["text"]} for l, ad in items])
        judged += len(items)
        for l, ad in items:
            v = verdicts.get(f"{l['lead_key']}:{ad['job_id']}")
            if v is None:
                stats["not_judged"] += 1
                continue
            stats["judged"] += 1
            if not v.ok:
                stats["failed_haiku"] += 1
                continue
            if company_ok(l):
                good.setdefault(l["employer_norm"], []).append((l, v, ad))

    def best(items):
        return max(items, key=lambda t: (t[0]["posting_count_90d"], t[0]["score"], t[0]["last_seen_at"]))
    ranked = sorted(good.items(), key=lambda kv: (-best(kv[1])[0]["posting_count_90d"], -best(kv[1])[0]["score"]))
    rows: list[dict] = []
    for norm, items in ranked[:owed]:
        lead, verdict, ad = best(items)
        c = contacts.get(norm) or store.lead_contacts_for([norm]).get(norm)
        if tools.ch is not None and not (c or {}).get("contacts_checked"):
            try:   # step 6: directors (free) + Brave/Hunter (capped)
                c = enrich_lead_company(store, lead["employer_name"], norm, tools.ch, tools.brave, tools.hunter)
            except Exception as exc:
                log.warning("decision maker for %s failed: %s", lead["employer_name"], exc)
        others = sorted({v.role for _, v, _ in items if v.role.lower() != verdict.role.lower()})
        rows.append(build_row(store, order, lead, verdict, ad, others, c, now))
    log.info("order %s: %s -> %d rows", order["id"], stats, len(rows))
    return rows, stats


def build_row(store, order: dict, lead: dict, verdict: Verdict, ad: dict, also: list[str], contact: dict | None,
              now: dt.datetime) -> dict:
    from .lead_contacts import email_for_export
    from .leads import suggested_email
    dates = sorted({d for d in (_as_date(x) for x in (lead.get("posting_dates") or [])) if d})
    first = dates[0] if dates else lead["first_seen_at"].date()
    n = max(int(lead["posting_count_90d"] or 1), len(dates))
    why = [f"Advertised {n} separate times" + (f" ({', '.join(month(d) for d in dates)})" if len(dates) > 1 else "")]
    if lead.get("flagged_repost"):
        why.append("the job board marks it as a repost")
    if (lead.get("archive_prior") or 0) > 0:
        why.append("older copies are in the web archive")
    why.append(f"still advertised {lead['last_seen_at']:%d %b}")
    if contact and contact.get("verified_company"):
        why.append("active company on Companies House")
    reason = "; ".join(why) + "." + (f" Check: {verdict.reason}" if verdict.reason else "")
    template = store.template_by_channel("client_hm_email")
    email_lead = {**lead, "employer_name": lead["employer_name"], "title": verdict.role,
                  "first_seen_at": dt.datetime.combine(first, dt.time(), tzinfo=dt.timezone.utc), "posting_count_90d": n}
    return {
        "lead_key": lead["lead_key"],
        "Date added": now.date().isoformat(),
        "Company": lead["employer_name"],
        "Role": verdict.role or ad["title"],
        "Also hiring": ", ".join(also[:3]),
        "Salary": ad.get("salary_text") or money(ad["min_salary"], ad["max_salary"]),
        "Times posted": n,
        "Posted on": ", ".join(d.strftime("%d %b %Y") for d in dates) if dates else first.strftime("%d %b %Y"),
        "Location": place(ad["location_name"] or lead.get("location_name")),
        "Ad link": ad["url"] or lead.get("latest_url") or "",
        "Previous ad": next((u for u in (lead.get("previous_url"), lead.get("old_ad_url")) if u and u != ad["url"]), ""),
        "Why it's a lead": reason,
        "Decision maker": (contact or {}).get("contact_name") or "",
        "Their role": (contact or {}).get("contact_role") or "",
        "Email": email_for_export(contact),
        "Suggested email": suggested_email(template, email_lead, contact, niche_words(order["industry"]), order["location"]),
        "Status": "new",
    }
