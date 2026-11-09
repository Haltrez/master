"""Personalise leads that are already in the Smartlead campaigns (imported there from Smartlead Prospect).

For each lead:
  1. agency check + niche: Claude Haiku reads the agency's homepage once (cached per domain)
  2. town -> the nearest of the cities we collect (HUBS, within HUB_MILES), else their own town
  3. a territory (niche + city) is watched, so the 06:00 collection finds its reposted roles
  4. three hot leads from that territory (verified direct employers, posted 2+ times), rotated so
     agencies in the same city get different companies
  5. written to Smartlead as custom fields (POST /campaigns/{id}/leads/{lead_id}, fields merge)

Leads Smartlead hasn't emailed yet (status STARTED) are refreshed every morning with the newest hot leads;
leads already in the sequence keep theirs so the follow-ups match the first email. Leads that can't be
filled are 'held' with the reason. Smartlead's API documents no way to pause one lead, so held leads are
listed (and exported) for you to remove in Smartlead before starting a campaign.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import re
from typing import Any

import httpx
from psycopg.types.json import Jsonb

from .crm_lists import domain_of
from .industries import INDUSTRIES
from .templates import last_active_label, month_name

log = logging.getLogger("personalise")

MODEL = "claude-haiku-5-5"
NICHE_WORD = {"Sales & Marketing": "sales & marketing", "Accountancy & Finance": "accountancy", "IT & Tech": "IT",
              "Engineering & Manufacturing": "engineering", "Construction & Property": "construction",
              "Building Services (M&E)": "M&E", "HR": "HR", "Legal": "legal", "Healthcare & Care": "healthcare",
              "Logistics & Warehouse": "logistics", "Office & Admin": "office", "Hospitality": "hospitality",
              "Education": "education"}
HUBS = ["London", "Manchester", "Birmingham", "Leeds", "Bristol", "Glasgow", "Edinburgh", "Liverpool", "Sheffield",
        "Newcastle upon Tyne", "Nottingham", "Leicester", "Cardiff", "Belfast", "Southampton", "Reading", "Cambridge",
        "Norwich", "Milton Keynes", "Aberdeen", "Brighton", "Oxford", "Coventry", "Hull", "Plymouth", "Exeter"]
HUB_MILES = 45          # small towns join the nearest big city within this distance
MIN_AGENCIES = 2        # a NEW territory is collected only when at least this many campaign agencies need it
TERRITORY_RADIUS = 25
FIELDS = ("niche", "city", "company_name", *(f"lead{i}_{v}" for i in (1, 2, 3) for v in ("employer", "role", "count", "since", "readvertised")))
SUFFIX = re.compile(r"[\s,]*\b(ltd\.?|limited|llp|plc|uk ltd|\(uk\))\s*$", re.I)

SCHEMA = {"type": "object", "additionalProperties": False, "required": ["is_agency", "niche", "why"],
          "properties": {"is_agency": {"type": "boolean"},
                         "niche": {"type": "string", "enum": [*INDUSTRIES.keys(), "none"]},
                         "why": {"type": "string"}}}
SYSTEM = ("You classify UK companies for a cold email to recruitment agencies. is_agency: true ONLY if the website "
          "clearly shows a recruitment / staffing / executive search business that places candidates with client "
          "companies: words like recruitment, recruiter, candidates, vacancies, 'submit your CV', 'for employers' or "
          "'for clients', placements, temp/perm. The word 'agency' alone is not enough: creative, production, "
          "marketing, design, digital, talent management (actors, models, influencers), events and PR agencies are "
          "NOT recruitment agencies. Also false for in-house HR teams, training providers, job boards, consultancies "
          "and software firms. If the site couldn't be loaded, decide from the name and title only when they say "
          "recruitment plainly; otherwise false. "
          "niche: the ONE industry the agency mainly recruits for, from the list; if it covers several, the one "
          "most prominent on the site; 'none' if not an agency or you can't tell. why: under 15 words.")


def clean_company(name: str | None) -> str:
    n = (name or "").strip()
    for _ in range(2):
        n = SUFFIX.sub("", n).strip()
    return n


def _homepage(domain: str, http: httpx.Client | None = None) -> str:
    if not domain:
        return ""
    own = http is None
    http = http or httpx.Client(timeout=12, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0 (compatible; SmartEmploy)"})
    try:
        for url in (f"https://{domain}", f"https://www.{domain}", f"http://{domain}"):
            try:
                r = http.get(url)
            except (httpx.HTTPError, ValueError, UnicodeError):
                continue
            if r.status_code < 400 and "html" in r.headers.get("content-type", "html"):
                try:
                    raw = r.text
                except Exception:
                    continue
                title = re.search(r"(?is)<title[^>]*>(.*?)</title>", raw)
                desc = re.search(r'(?is)<meta[^>]+name="description"[^>]+content="([^"]*)"', raw)
                body = re.sub(r"(?is)<(script|style|noscript|svg)[^>]*>.*?</\1>", " ", raw)
                body = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body))
                return f"Title: {title.group(1).strip() if title else ''}\nDescription: {desc.group(1) if desc else ''}\n{body[:3000]}"
        return ""
    finally:
        if own:
            http.close()


def classify(store, client, lead: dict, fetch=None) -> dict:
    """{'is_agency', 'niche', 'why'}; cached per domain (or company name) forever."""
    key = "agencyniche2:" + (lead.get("domain") or (lead.get("company_name") or "").lower())
    cached = store.cache_get(key)
    if cached:
        return cached
    if client is None:
        return {"is_agency": None, "niche": "none", "why": "no Claude key on the server"}
    page = (fetch or _homepage)(lead.get("domain") or "")
    prompt = (f"Company: {lead.get('company_name')}\nWebsite: {lead.get('domain') or 'unknown'}\n"
              f"Person's title: {lead.get('title') or ''}\nLinkedIn sector: {lead.get('sector') or ''}\n\n"
              f"Homepage text:\n{page or '(could not load the website)'}")
    resp = client.messages.create(model=MODEL, max_tokens=600, system=SYSTEM,
                                  output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
                                  messages=[{"role": "user", "content": prompt}])
    if resp.stop_reason in ("refusal", "max_tokens"):
        return {"is_agency": None, "niche": "none", "why": f"Claude stopped ({resp.stop_reason})"}
    out = json.loads("".join(b.text for b in resp.content if getattr(b, "type", None) == "text"))
    store.cache_set(key, out)
    return out


PUBLIC_DOMAINS = re.compile(r"(\.gov\.uk|\.nhs\.(uk|net)|\.ac\.uk|\.sch\.uk|\.police\.uk|\.mod\.uk|\.gov)$")
FREEMAIL = {"gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "hotmail.co.uk", "yahoo.com", "yahoo.co.uk",
            "icloud.com", "live.com", "live.co.uk", "aol.com", "btinternet.com", "me.com", "protonmail.com"}


def email_problem(email: str, website_domain: str | None) -> str | None:
    """Why this address probably isn't the person at the agency now (Prospect data can be out of date)."""
    dom = (email or "").rsplit("@", 1)[-1].lower()
    if PUBLIC_DOMAINS.search(dom):
        return f"email is at {dom}, not the agency: they've probably moved on"
    if dom in FREEMAIL:
        return f"personal email ({dom}), not an agency address"
    site = (website_domain or "").lower()
    if site and not (dom == site or dom.endswith("." + site) or site.endswith("." + dom)):
        def root(d):
            return d.split(".")[0]
        if root(dom) != root(site):
            return f"email is at {dom} but the agency's website is {site}: probably moved on"
    return None


def town_of(location: str | None) -> str:
    first = (location or "").split(",")[0].strip()
    return "" if first.lower() in ("united kingdom", "uk", "england", "scotland", "wales", "") else first


def hub_for(geocoder, town: str) -> str:
    """The nearest collected city within HUB_MILES, else the town itself (it gets its own territory)."""
    if not town:
        return ""
    if geocoder is None:
        return town
    from .geo import miles_between
    p = geocoder.geocode(town + ", UK")
    if p is None:
        return town
    best, dist = "", 1e9
    for h in HUBS:
        q = geocoder.geocode(h + ", UK")
        if q is not None:
            d = miles_between((p.lat, p.lng), (q.lat, q.lng))
            if d < dist:
                best, dist = h, d
    return best if dist <= HUB_MILES else town


def ch_town(ch, company: str) -> str:
    """Registered office town from Companies House, for leads whose location is just 'United Kingdom'."""
    if ch is None or not company:
        return ""
    from .agencies import best_match
    try:
        best, conf = best_match(company, ch.search(company))
        if best is None or conf < 0.85:
            return ""
        addr = (ch.profile(best["company_number"]) or {}).get("registered_office_address") or {}
        return (addr.get("locality") or addr.get("region") or "").strip()
    except Exception as exc:
        log.info("companies house town for %s: %s", company, exc)
        return ""


def hot_leads(store, search_id: int) -> list[dict]:
    return store.q("""select h.* from hot_leads h join hot_lead_searches hs on hs.lead_key = h.lead_key
                      where hs.search_id = %s and h.status <> 'ignored' and h.signal = 'active' and h.posting_count_90d >= 2
                        and exists (select 1 from lead_contacts c where c.employer_norm = h.employer_norm and c.verified_company)
                      order by h.score desc, h.posting_count_90d desc, h.last_seen_at desc limit 60""", (search_id,))


def no_paid_lookups(tools):
    """The order tools without Brave/Hunter: cold emails only need the hot leads, not their contacts."""
    import dataclasses
    return dataclasses.replace(tools, brave=None, hunter=None) if dataclasses.is_dataclass(tools) else tools


def checked_pool(store, tools, search_id: int, niche: str, owed: int = 30) -> list[dict]:
    """The territory's hot leads after the same checks a client order gets: in the area, Claude says the ad
    really is a {niche} job at a direct employer, verified company. Kept for a day per territory."""
    from .leadgen import pick
    key = f"pzpool2:{search_id}"
    cached = store.cache_get(key, max_age_days=0.9)
    if cached is not None:
        keys = cached.get("keys") or []
        rows = {h["lead_key"]: h for h in store.q("select * from hot_leads where lead_key = any(%s)", (keys,))}
        return [{**rows[k], "role": cached.get("roles", {}).get(k) or rows[k]["title"]} for k in keys if k in rows]
    order = {"id": f"campaign-{search_id}", "search_id": search_id, "industry": niche, "client_id": None, "agency_id": None}
    leads, stats = pick(store, order, tools, owed, leads_only=True)
    store.cache_set(key, {"keys": [h["lead_key"] for h in leads], "roles": {h["lead_key"]: h["role"] for h in leads},
                          "stats": stats})
    return leads


LEAD1_MIN_POSTS = 3       # the headline company: posted 3+ times ("3x reposted ..."), else the lead waits
MAX_AS_LEAD1 = 2          # a company is the headline in at most 2 emails per run
MAX_USES = 3              # and appears in at most 3 emails per run


def _pick3(pool: list[dict], used: dict[str, int], lead1_used: dict[str, int]) -> tuple[list[dict], str]:
    """Lead 1: the least-used company posted 3+ times; leads 2 and 3: the least-used others. Each company
    is spread across agencies (MAX_AS_LEAD1 / MAX_USES), so neighbours don't get the same email.
    Returns (three leads, '') or ([], why not)."""
    def n(h):
        return int(h["posting_count_90d"] or 0)
    def free(h):
        return used.get(h["employer_norm"], 0) < MAX_USES
    heads = [h for h in pool if n(h) >= LEAD1_MIN_POSTS and free(h) and lead1_used.get(h["employer_norm"], 0) < MAX_AS_LEAD1]
    if not heads:
        strong = sum(1 for h in pool if n(h) >= LEAD1_MIN_POSTS)
        return [], (f"no company posted {LEAD1_MIN_POSTS}+ times yet" if not strong
                    else "its strongest hot leads are already in other agencies' emails")
    first = min(heads, key=lambda h: (lead1_used.get(h["employer_norm"], 0), used.get(h["employer_norm"], 0), -n(h),
                                      -float(h.get("score") or 0)))
    rest = [h for h in pool if free(h) and h["employer_norm"] != first["employer_norm"]]
    rest.sort(key=lambda h: (used.get(h["employer_norm"], 0), -n(h), -float(h.get("score") or 0)))
    out, seen = [first], {first["employer_norm"]}
    for h in rest:
        if h["employer_norm"] not in seen:
            seen.add(h["employer_norm"])
            out.append(h)
        if len(out) == 3:
            break
    if len(out) < 3:
        return [], "not enough different hot leads yet"
    for h in out:
        used[h["employer_norm"]] = used.get(h["employer_norm"], 0) + 1
    lead1_used[first["employer_norm"]] = lead1_used.get(first["employer_norm"], 0) + 1
    return out, ""


def lead_fields(niche: str, city: str, company: str, leads: list[dict]) -> dict:
    f: dict[str, Any] = {"niche": NICHE_WORD.get(niche, niche), "city": city, "company_name": clean_company(company)}
    for i, h in enumerate(leads[:3], 1):
        f[f"lead{i}_employer"] = clean_company(h["employer_name"])
        f[f"lead{i}_role"] = h.get("role") or h["title"]
        f[f"lead{i}_count"] = str(h["posting_count_90d"])
        f[f"lead{i}_since"] = month_name(h["first_seen_at"])
        f[f"lead{i}_readvertised"] = last_active_label(h)
    return f


# ---------------------------------------------------------------------------
def sync(store, sl, cids: dict[str, int | None]) -> int:
    """Copy both campaigns' leads into campaign_leads (status, contact details). Returns the count."""
    n = 0
    for role, cid in cids.items():
        if not cid:
            continue
        for r in sl.campaign_leads(cid):
            L = r.get("lead") or {}
            cf = L.get("custom_fields") or {}
            if not L.get("id") or not L.get("email"):
                continue
            store.q("""insert into campaign_leads (campaign_id, lead_id, campaign_role, map_id, email, first_name, company_name,
                         website, location, title, sl_status, domain, synced_at)
                       values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
                       on conflict (campaign_id, lead_id) do update set campaign_role = excluded.campaign_role,
                         map_id = excluded.map_id, email = excluded.email, first_name = excluded.first_name,
                         company_name = excluded.company_name, website = excluded.website, location = excluded.location,
                         title = excluded.title, sl_status = excluded.sl_status, domain = excluded.domain, synced_at = now()""",
                    (cid, int(L["id"]), role, r.get("campaign_lead_map_id"), L["email"].strip().lower(),
                     (L.get("first_name") or "").strip(), L.get("company_name"), L.get("website") or L.get("company_url"),
                     L.get("location"), cf.get("title") or "", r.get("status"),
                     domain_of(L.get("website") or L.get("company_url") or "") or None))
            store.q("update campaign_leads set fields = fields || %s where campaign_id = %s and lead_id = %s and fields = '{}'",
                    (Jsonb({"_sector": cf.get("sector") or ""}), cid, int(L["id"])))
            n += 1
    return n


def _hold(store, r: dict, reason: str, **extra) -> None:
    store.q("""update campaign_leads set state = 'held', reason = %s, town = %s, hub = %s, niche = %s, is_agency = %s,
               search_id = %s where campaign_id = %s and lead_id = %s""",
            (reason, extra.get("town", r.get("town")), extra.get("hub", r.get("hub")), extra.get("niche", r.get("niche")),
             extra.get("is_agency", r.get("is_agency")), extra.get("search_id", r.get("search_id")), r["campaign_id"], r["lead_id"]))


def prepare(store, client, geocoder=None, ch=None, fetch=None) -> dict:
    """Agency check, niche, town and territory for leads that don't have them yet (no Smartlead writes)."""
    from .orders import watch_territory
    rows = store.q("select * from campaign_leads where niche is null or (hub is null and niche <> 'none')")
    done, new_territories = 0, set()
    for r in rows:
        bad = email_problem(r["email"], r.get("domain"))
        if bad:
            _hold(store, r, bad, niche="none")
            continue
        loc = (r.get("location") or "").lower()
        if loc and not any(w in loc for w in ("united kingdom", "england", "scotland", "wales", "northern ireland", ", uk")):
            _hold(store, r, f"outside the UK ({r.get('location')})"[:200], niche="none")
            continue
        sector = (r.get("fields") or {}).get("_sector") or ""
        try:
            v = classify(store, client, {**r, "sector": sector}, fetch=fetch)
        except Exception as exc:          # one odd website or API hiccup never stops the run; tried again next run
            log.warning("classify %s: %s", r.get("domain"), exc)
            _hold(store, r, f"couldn't check the website yet ({type(exc).__name__}); tried again next run")
            store.q("update campaign_leads set niche = null where campaign_id = %s and lead_id = %s", (r["campaign_id"], r["lead_id"]))
            continue
        if not v.get("is_agency"):
            _hold(store, r, f"not a recruitment agency ({v.get('why') or 'Claude'})"[:200], is_agency=v.get("is_agency"),
                  niche="none")
            continue
        niche = v.get("niche") or "none"
        if niche not in INDUSTRIES:
            _hold(store, r, "couldn't tell which niche they recruit for", is_agency=True, niche="none")
            continue
        town = town_of(r.get("location")) or ch_town(ch, r.get("company_name") or "")
        if not town:
            _hold(store, r, "no town (location is just the country, not on Companies House)", is_agency=True, niche=niche)
            continue
        try:
            hub = hub_for(geocoder, town)
        except Exception as exc:
            log.warning("hub for %s: %s", town, exc)
            hub = town
        store.q("""update campaign_leads set is_agency = true, niche = %s, town = %s, hub = %s, search_id = null
                   where campaign_id = %s and lead_id = %s""", (niche, town, hub, r["campaign_id"], r["lead_id"]))
        done += 1
    # Territories: one per niche + city, collected every morning. A new one only when 2+ of your agencies need it
    # (or it's already collected for an order / sold territory): a territory for a single agency costs a whole
    # collection (job boards, Indeed via Apify, Claude ad checks) for one email.
    groups = store.q("""select niche, hub, count(distinct coalesce(domain, lower(company_name))) as agencies
                        from campaign_leads where is_agency and niche <> 'none' and hub is not null group by 1, 2""")
    for g in groups:
        label = f"{g['niche']} - {g['hub']}"
        existing = store.one("""select s.id from searches s where lower(s.label) = lower(%s) and s.active and (
                                  exists (select 1 from lead_orders o where o.search_id = s.id)
                                  or exists (select 1 from client_territories t where t.search_id = s.id))""", (label,))
        if existing or int(g["agencies"]) >= MIN_AGENCIES:
            sid = existing["id"] if existing else watch_territory(store, g["niche"], g["hub"], radius_miles=TERRITORY_RADIUS)["search_id"]
            new_territories.add(label)
            store.q("""update campaign_leads set search_id = %s where niche = %s and hub = %s and is_agency
                       and search_id is distinct from %s""", (sid, g["niche"], g["hub"], sid))
        else:
            store.q("""update campaign_leads set search_id = null, state = 'held',
                         reason = %s where niche = %s and hub = %s and is_agency""",
                    (f"only agency for {NICHE_WORD.get(g['niche'], g['niche'])} around {g['hub']}: not collected "
                     f"(a city + niche is collected once {MIN_AGENCIES}+ of your agencies need it)", g["niche"], g["hub"]))
    switched_off = _switch_off_lonely_territories(store)
    return {"prepared": done, "territories": sorted(new_territories), "territories_switched_off": switched_off}


def _switch_off_lonely_territories(store) -> list[str]:
    """Territories made for campaign leads that no longer have 2+ agencies, and no order or sold territory."""
    first = store.val("select min(synced_at) from campaign_leads")
    if first is None:
        return []
    rows = store.q("""select s.id, s.label from searches s
                      where s.active and s.created_at >= %s - interval '1 hour'
                        and not exists (select 1 from lead_orders o where o.search_id = s.id)
                        and not exists (select 1 from client_territories t where t.search_id = s.id)
                        and not exists (select 1 from campaign_leads c where c.search_id = s.id)
                        and exists (select 1 from campaign_leads c
                                    where lower(c.niche || ' - ' || c.hub) = lower(s.label))""", (first,))
    for r in rows:
        store.q("update searches set active = false where id = %s", (r["id"],))
    if rows:
        log.info("campaign territories switched off (fewer than %d agencies): %s", MIN_AGENCIES, [r["label"] for r in rows])
    return [r["label"] for r in rows]


def fill(store, sl, *, only_unsent: bool = True, dry_run: bool = False, pool=None) -> dict:
    """Pick hot leads and write the custom fields. Leads already emailed (status not STARTED) keep theirs."""
    rows = store.q("""select * from campaign_leads where is_agency and search_id is not null and niche <> 'none'
                      order by hub, map_id nulls last, lead_id""")
    pools: dict[int, list[dict]] = {}
    used: dict[str, int] = {}
    lead1_used: dict[str, int] = {}
    out = {"filled": 0, "unchanged": 0, "held": 0, "kept_in_sequence": 0, "errors": []}
    for r in rows:
        if only_unsent and r["state"] == "filled" and (r["sl_status"] or "STARTED") != "STARTED":
            out["kept_in_sequence"] += 1
            continue
        if not (r.get("first_name") or "").strip():
            _hold(store, r, "no first name")
            out["held"] += 1
            continue
        sid = r["search_id"]
        if sid not in pools:
            try:
                pools[sid] = (pool or (lambda s_id, n: hot_leads(store, s_id)))(sid, r["niche"])
            except Exception as exc:
                log.warning("hot leads for %s: %s", sid, exc)
                pools[sid] = []
        picked, why = _pick3(pools[sid], used, lead1_used)
        if not picked:
            _hold(store, r, f"waiting for hot leads: {NICHE_WORD.get(r['niche'], r['niche'])} around {r['hub']} has "
                            f"{len(pools[sid])} checked so far, {why} (collection adds more every morning)")
            out["held"] += 1
            continue
        f = lead_fields(r["niche"], r["hub"], r["company_name"] or "", picked)
        old = {k: v for k, v in (r.get("fields") or {}).items() if not k.startswith("_")}
        if old == f and r["state"] == "filled":
            out["unchanged"] += 1
            continue
        if not dry_run:
            try:
                custom = {k: v for k, v in f.items() if k != "company_name"}
                sl.update_lead(r["campaign_id"], r["lead_id"], r["email"], custom, company_name=f["company_name"])
            except Exception as exc:
                out["errors"].append(f"{r['email']}: {exc}"[:200])
                continue
        keep = {k: v for k, v in (r.get("fields") or {}).items() if k.startswith("_")}
        store.q("""update campaign_leads set fields = %s, state = 'filled', reason = null, filled_at = now()
                   where campaign_id = %s and lead_id = %s""", (Jsonb({**keep, **f, "_lead_keys": [h["lead_key"] for h in picked]}),
                                                                r["campaign_id"], r["lead_id"]))
        out["filled"] += 1
    return out


def run(store, settings, sl, *, client=None, geocoder=None, ch=None, fetch=None, dry_run: bool = False,
        tools=None, pool=None) -> dict:
    """Sync -> prepare -> fill. Safe to run again: it only writes what changed."""
    from .outreach import campaign_ids
    cids = campaign_ids(store)
    if not any(cids.values()):
        raise ValueError("no campaigns chosen: pick both in Settings → Outreach and press Save")
    synced = sync(store, sl, cids)
    prep = prepare(store, client, geocoder=geocoder, ch=ch, fetch=fetch)
    if pool is None and tools is not None and getattr(tools, "claude", None) is not None:
        pool = lambda sid, niche: checked_pool(store, tools, sid, niche)   # noqa: E731
    if pool is None:
        raise ValueError("ANTHROPIC_API_KEY is needed on the server: every hot lead is checked by Claude before it goes in an email")
    res = fill(store, sl, dry_run=dry_run, pool=pool)
    out = {"synced": synced, **prep, **res, "at": dt.datetime.now(dt.timezone.utc).isoformat()}
    store.cache_set("personalise:last", out)
    log.info("personalise: %s", {k: v for k, v in out.items() if k != "territories"})
    return out


def summary(store, live: set[int] | None = None) -> dict:
    by = store.q("""select campaign_role, state, count(*) as n from campaign_leads group by 1, 2""")
    reasons = store.q("""select coalesce(regexp_replace(reason, ' \\(.*$', ''), '') as reason, count(*) as n
                         from campaign_leads where state = 'held' group by 1 order by 2 desc limit 20""")
    totals: dict[str, dict[str, int]] = {}
    for r in by:
        totals.setdefault(r["campaign_role"] or "?", {})[r["state"]] = int(r["n"])
    run = store.one("""select id, started_at from tasks where kind = 'personalise' and status = 'running'
                       and started_at > now() - interval '3 hours' and id = any(%s) order by id desc limit 1""",
                    (list(live) if live is not None else [],))
    prog = store.one("""select count(*) as total, count(*) filter (where niche is not null) as checked from campaign_leads""")
    last_task = store.one("""select status, error, started_at, finished_at from tasks where kind = 'personalise'
                             and status <> 'running' order by id desc limit 1""")
    return {"last_task": {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in last_task.items()} if last_task else None,
            "running": {"task_id": run["id"], "since": run["started_at"].isoformat(), "checked": int(prog["checked"]),
                        "total": int(prog["total"])} if run else None,
            "by_campaign": totals, "held_reasons": [{"reason": r["reason"], "n": int(r["n"])} for r in reasons],
            "last_run": store.cache_get("personalise:last"),
            "territories": store.q("""select hub, niche, count(*) as n from campaign_leads where hub is not null and niche <> 'none'
                                      group by 1, 2 order by 3 desc limit 60""")}


def leads(store, state: str | None = None, limit: int = 200) -> list[dict]:
    return store.q("""select campaign_id, lead_id, campaign_role, email, first_name, company_name, title, location, town, hub,
                             niche, state, reason, sl_status, fields from campaign_leads
                      where (%s::text is null or state = %s) order by state, hub, company_name limit %s""", (state, state, limit))


def preview(store, sl, campaign_id: int, lead_id: int) -> dict:
    from .outreach import _text, fill as fill_vars, sequence
    r = store.one("select * from campaign_leads where campaign_id = %s and lead_id = %s", (campaign_id, lead_id))
    if r is None:
        raise ValueError("lead not found")
    f = {**(r.get("fields") or {}), "first_name": r["first_name"], "email": r["email"]}
    steps = sequence(store, sl, campaign_id)
    return {"lead": {k: r[k] for k in ("email", "company_name", "state", "reason", "hub", "niche")},
            "steps": [{"seq_number": s["seq_number"], "subject": fill_vars(s["subject"], f), "body": _text(fill_vars(s["body"], f))}
                      for s in steps]}


def held_csv(store) -> str:
    import csv
    import io
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["email", "campaign", "company", "reason"])
    for r in store.q("select email, campaign_role, company_name, reason from campaign_leads where state = 'held' order by 2, 3"):
        w.writerow([r["email"], r["campaign_role"], r["company_name"], r["reason"]])
    return buf.getvalue()
