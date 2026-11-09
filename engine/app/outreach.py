"""Cold email to recruitment agencies through Smartlead: the daily queue (the feeder), push, replies, health.

Feeder: agencies we haven't contacted, each with 1-2 of their own hot leads (company, role, times posted, since)
as custom fields. Two campaigns by who reads the email: "leaders" (founders, owners, directors, CEOs, managers)
and "sales" (recruitment consultants, business development, account managers). An agency can have one row in
each: the same leads go to both people, and a reply from either pauses the other in Smartlead. A hot lead is pitched to one agency per city per day
(lead_assignments). Rows that can't go out are kept, marked blocked, with the reason, and are never pushed.
"""

from __future__ import annotations

import datetime as dt
import html as _html
import logging
import re
from typing import Any
from zoneinfo import ZoneInfo

from psycopg.types.json import Jsonb

from .agencies import email_allowed
from .pg import email_hash
from .templates import last_active_label, month_name, niche_and_city

log = logging.getLogger("outreach")
UK = ZoneInfo("Europe/London")

S_LEADERS, S_SALES = "outreach_leaders_campaign_id", "outreach_sales_campaign_id"
S_OLD_MAIN = "outreach_main_campaign_id"     # before the leaders/sales split: read as the leaders campaign
ROLES = ("leaders", "sales")
ROLE_LABEL = {"leaders": "Founders & managers", "sales": "Sales people"}
LEAD_VARS = ("employer", "role", "count", "since")
S_LIMIT, S_AUTO, S_WINDOW = "outreach_daily_push_limit", "outreach_auto_push", "outreach_send_window"
BOUNCE_PAUSE = 0.03          # auto-pause above 3% bounces ...
BOUNCE_WINDOW = 200          # ... in the last 200 sends
BOUNCE_MIN_SENDS = 30        # (not on a handful of sends: 1 bounce in 10 is noise, not a trend)
STANDARD_VARS = {"first_name", "last_name", "email", "company_name"}
VAR = re.compile(r"\{\{\s*([A-Za-z0-9_.]+)\s*\}\}")
CATEGORIES = ("interested", "not_now", "not_interested", "unsubscribe", "out_of_office", "wrong_person")


def uk_today() -> dt.date:
    return dt.datetime.now(UK).date()


def setting(store, key: str, default: Any = None) -> Any:
    v = store.setting(key, default)
    return default if v is None else v


def campaign_ids(store) -> dict[str, int | None]:
    def num(v):
        try:
            return int(v) if v not in (None, "") else None
        except (TypeError, ValueError):
            return None
    return {"leaders": num(setting(store, S_LEADERS)) or num(setting(store, S_OLD_MAIN)),
            "sales": num(setting(store, S_SALES))}


SALES_TITLES = re.compile(r"recruit|consultant|business develop|\bbdm?\b|\bbde\b|account|sales|talent acquisition|"
                          r"resourcer|placement|client (partner|executive)|\bexecutive\b", re.I)
TOP_TITLES = re.compile(r"founder|owner|\bceo\b|chief|\bmd\b|managing|^partner$|(founding|equity|senior) partner|president|proprietor|chair", re.I)
MANAGER_TITLES = re.compile(r"manager|director|\bhead\b|\blead\b|principal|\bboss\b", re.I)
SALES_MANAGER_JOBS = re.compile(r"account (manager|director)|business development (manager|director)|\bbdm\b", re.I)


def audience(title: str | None) -> str:
    """Which campaign a person belongs to, from their job title.
    leaders: founders, owners, CEOs, MDs, directors, managers, heads of ('Sales Manager' runs a team: leader).
    sales:   recruitment consultants, recruiters, resourcers, account managers, business development.
    No title counts as a leader (a Companies House officer is 'Director'). Anyone else (an office administrator,
    a marketing assistant) is 'other' and isn't emailed."""
    t = (title or "").strip()
    if not t or TOP_TITLES.search(t):
        return "leaders"
    if SALES_MANAGER_JOBS.search(t):
        return "sales"            # 'Account Manager' / 'BDM' sell; they don't manage people
    if MANAGER_TITLES.search(t):
        return "leaders"
    return "sales" if SALES_TITLES.search(t) else "other"
    if SALES_TITLES.search(t):
        return "sales"
    return "leaders"


# ---------------------------------------------------------------------------
# email text: the campaign's own sequence, filled in
# ---------------------------------------------------------------------------
def sequence(store, sl, campaign_id: int | None) -> list[dict]:
    if not campaign_id or sl is None:
        return []
    key = f"outreach:seq:{campaign_id}"
    cached = store.cache_get(key, max_age_days=15 / 1440)
    if cached is not None:
        return cached.get("steps", [])
    try:
        steps = sl.sequences(campaign_id)
    except Exception as exc:
        log.warning("sequences for campaign %s: %s", campaign_id, exc)
        return []
    steps = sorted([{"seq_number": s.get("seq_number"), "subject": s.get("subject") or "",
                     "body": s.get("email_body") or ""} for s in steps], key=lambda s: s["seq_number"] or 0)
    store.cache_set(key, {"steps": steps})
    return steps


def variables_in(steps: list[dict]) -> set[str]:
    out: set[str] = set()
    for s in steps:
        out |= {v.split(".")[-1] for v in VAR.findall((s.get("subject") or "") + " " + (s.get("body") or ""))}
    return out


def _text(html: str) -> str:
    t = re.sub(r"(?i)<br\s*/?>|</p>|</div>", "\n", html or "")
    t = _html.unescape(re.sub(r"<[^>]+>", "", t))
    return re.sub(r"\n{3,}", "\n\n", t).strip()


def fill(text: str, fields: dict) -> str:
    return VAR.sub(lambda m: str(fields.get(m.group(1).split(".")[-1], "") or ""), text or "")


def lead_payload(fields: dict, email: str) -> dict:
    return {"email": email, "first_name": fields.get("first_name") or "", "last_name": fields.get("last_name") or "",
            "company_name": fields.get("company_name") or fields.get("agency") or "",
            "custom_fields": {k: ("" if v is None else str(v)) for k, v in fields.items()
                              if k not in STANDARD_VARS and not k.startswith("_")}}


def preview(store, sl, row: dict) -> dict:
    cids = campaign_ids(store)
    cid = cids.get(row["campaign_role"])
    steps = sequence(store, sl, cid)
    f = {**row["fields"], "email": row["fields"].get("_email", "")}
    if not steps:
        from .templates import render
        t = store.template_by_channel("email")
        if not t:
            return {"campaign_id": cid, "steps": [], "note": "No sequence in this Smartlead campaign yet, and no CRM email template."}
        d = render(t, f)
        return {"campaign_id": cid, "note": "This campaign has no sequence in Smartlead yet: showing the CRM's email template.",
                "steps": [{"seq_number": 1, "subject": d.subject or "", "body": d.body}]}
    return {"campaign_id": cid, "steps": [{"seq_number": s["seq_number"], "subject": fill(s["subject"], f),
                                           "body": _text(fill(s["body"], f))} for s in steps]}


# ---------------------------------------------------------------------------
# the feeder
# ---------------------------------------------------------------------------
def _contacts(store, agency_id: int) -> dict[str, dict]:
    """The best person for each campaign at this agency: verified email first, then the most senior title."""
    cs = [c for c in store.contacts(agency_id) if c.get("email") and not c["do_not_contact"]]
    cs.sort(key=lambda c: (not c.get("email_verified"), not (TOP_TITLES.search(c.get("role") or "") or (c.get("role") or "Director") == "Director"), c["id"]))
    out: dict[str, dict] = {}
    for c in cs:
        role = audience(c.get("role"))
        if role in ROLES:
            out.setdefault(role, c)
    return out


def _leads_for(store, search_id: int, city: str, today: dt.date, taken: set[str], n: int = 2) -> list[dict]:
    used = {r["hot_lead_key"] for r in store.q("select hot_lead_key from lead_assignments where city = %s and date = %s",
                                                (city.lower(), today))}
    rows = store.q("""select h.* from hot_leads h join hot_lead_searches hs on hs.lead_key = h.lead_key
                      where hs.search_id = %s and h.status <> 'ignored' and h.signal = 'active'
                        and exists (select 1 from lead_contacts c where c.employer_norm = h.employer_norm and c.verified_company)
                      order by h.score desc, h.last_seen_at desc limit 40""", (search_id,))
    out, seen_emp = [], set()
    for h in rows:
        if h["lead_key"] in used or h["lead_key"] in taken or h["employer_norm"] in seen_emp:
            continue
        seen_emp.add(h["employer_norm"])
        out.append(h)
        if len(out) >= n:
            break
    return out


def _lead_fields(leads: list[dict]) -> dict:
    f: dict[str, Any] = {}
    for i, h in enumerate(leads[:2], 1):
        f[f"lead{i}_employer"] = h["employer_name"]
        f[f"lead{i}_role"] = h["title"]
        f[f"lead{i}_count"] = h["posting_count_90d"]
        f[f"lead{i}_since"] = month_name(h["first_seen_at"])
        f[f"lead{i}_readvertised"] = last_active_label(h)
    f["_lead_keys"] = [h["lead_key"] for h in leads[:2]]
    return f


def _in_smartlead(sl, email: str) -> bool:
    if sl is None:
        return False
    lead = sl.lead_by_email(email)
    if not lead or not lead.get("id"):
        return False
    return bool(sl.lead_campaigns(int(lead["id"])))


def build_queue(store, settings, sl=None, *, limit: int | None = None, today: dt.date | None = None) -> list[dict]:
    """Dry run: today's rows (pushable first). Rebuilding replaces today's unpushed rows."""
    today = today or uk_today()
    limit = int(limit or setting(store, S_LIMIT, 50))
    store.q("delete from outreach_queue where date = %s and status in ('built', 'blocked', 'approved') and not is_test", (today,))
    cids = campaign_ids(store)
    needed = {r: variables_in(sequence(store, sl, cids[r])) for r in ROLES}
    sender = str(setting(store, "sender_name", "Roberts"))
    suppressed = store.suppressed_hashes()
    pushed = {r["email"] for r in store.q("""select lower(fields->>'_email') as email from outreach_queue
                                             where status = 'pushed' and not is_test""")}
    taken_today: dict[str, set[str]] = {}
    agencies = store.q("""select a.* from agencies a
                          where a.stage in ('new', 'researching') and (a.next_follow_up is null or a.next_follow_up <= %s)
                            and exists (select 1 from agency_searches x join searches s on s.id = x.search_id where x.agency_id = a.id)
                          order by a.ads_count_90d desc nulls last, a.id limit 500""", (today,))
    good = blocked = 0
    for ag in agencies:
        if good >= limit:
            break
        people = _contacts(store, ag["id"])
        if not people:
            continue                     # nobody to email: not a row at all
        sids = store.agency_search_ids(ag["id"])
        search = store.search_row(sids[0]) if sids else None
        niche, city = niche_and_city(search)
        taken = taken_today.setdefault(city.lower(), set())
        leads = _leads_for(store, search["id"], city, today, taken) if search else []
        for role in ROLES:
            c = people.get(role)
            if c is None or good >= limit:
                continue
            first, *rest = (c.get("full_name") or "").split() or [""]
            fields = {"first_name": c.get("first_name") or first or "", "last_name": " ".join(rest[-1:]) if rest else "",
                      "company_name": ag["name"], "agency": ag["name"], "niche": niche, "city": city, "sender_name": sender,
                      "_email": c["email"], "_contact_name": c.get("full_name"), "_title": c.get("role") or "",
                      **_lead_fields(leads)}
            blocks, warns = [], []
            email = (c["email"] or "").strip().lower()
            if not c.get("email_verified"):
                blocks.append("email not verified")
            if c.get("do_not_contact") or ag["stage"] == "do_not_contact":
                blocks.append("do not contact")
            if email_hash(email) in suppressed:
                blocks.append("on the suppression list")
            if not email_allowed(ag):
                blocks.append("not a confirmed Ltd/LLP/PLC")
            if not leads:
                blocks.append("no unused hot lead in this city today")
            if email in pushed:
                blocks.append("already pushed to Smartlead")
            if not cids[role]:
                warns.append(f"no {ROLE_LABEL[role].lower()} campaign chosen in Settings → Outreach yet")
            need = needed[role] or {"first_name", "agency", "niche", "city", *(f"lead1_{v}" for v in LEAD_VARS)}
            empty = sorted(v for v in need if str({**fields, "email": email}.get(v, "") or "").strip() == "")
            if empty:
                if len(leads) == 1 and all(v.startswith("lead2_") for v in empty):
                    blocks.append("only 1 hot lead, but this campaign's email uses lead2_ variables")
                else:
                    blocks.append("empty variables: " + ", ".join(empty))
            if not blocks:
                try:
                    if _in_smartlead(sl, email):
                        blocks.append("already in a Smartlead campaign")
                except Exception as exc:
                    warns.append(f"couldn't check Smartlead for this email: {exc}"[:200])
            if blocks and blocked >= 30:
                continue
            status = "blocked" if blocks else "built"
            if not blocks:
                taken.update(fields["_lead_keys"])
                good += 1
            else:
                blocked += 1
            store.q("""insert into outreach_queue (date, agency_id, contact_id, campaign_role, fields, warnings, status)
                       values (%s, %s, %s, %s, %s, %s, %s)""",
                    (today, ag["id"], c["id"], role, Jsonb(fields), Jsonb(blocks + warns), status))
    return queue(store, today)


def queue(store, today: dt.date | None = None) -> list[dict]:
    return store.q("""select q.*, a.name as agency_name, a.stage, c.full_name as contact_name, c.email as contact_email
                      from outreach_queue q left join agencies a on a.id = q.agency_id
                      left join contacts c on c.id = q.contact_id
                      where q.date = %s and q.status <> 'removed' and not q.is_test
                      order by (q.status = 'blocked'), q.id""", (today or uk_today(),))


def row(store, rid: int) -> dict | None:
    return store.one("select * from outreach_queue where id = %s", (rid,))


def remove(store, rid: int) -> None:
    store.q("update outreach_queue set status = 'removed' where id = %s and status in ('built', 'approved', 'blocked')", (rid,))


def move(store, rid: int, role: str) -> None:
    if role not in ROLES:
        raise ValueError("campaign must be leaders or sales")
    store.q("update outreach_queue set campaign_role = %s where id = %s and status in ('built', 'approved')", (role, rid))


def swap_lead(store, rid: int, which: int = 1) -> dict:
    r = row(store, rid)
    if r is None or r["status"] not in ("built", "approved"):
        raise ValueError("row not found or already pushed")
    f = dict(r["fields"])
    sids = store.agency_search_ids(r["agency_id"])
    keys = list(f.get("_lead_keys") or [])
    others_today = {k for q in store.q("select fields from outreach_queue where date = %s and id <> %s and status in ('built', 'approved', 'pushed')",
                                       (r["date"], rid)) for k in (q["fields"].get("_lead_keys") or [])}
    tried = set(f.get("_swapped") or [])
    nxt = _leads_for(store, sids[0], f.get("city") or "", r["date"], set(keys) | others_today | tried, n=1) if sids else []
    if not nxt:
        raise ValueError("no other unused hot lead for this agency's city today")
    idx = max(0, min(which, len(keys)) - 1) if keys else 0
    old = keys[idx] if keys else None
    leads_by_key = {h["lead_key"]: h for h in store.q("select * from hot_leads where lead_key = any(%s)", (keys,))}
    current = [leads_by_key[k] for k in keys if k in leads_by_key]
    if current and idx < len(current):
        current[idx] = nxt[0]
    else:
        current.append(nxt[0])
    f.update(_lead_fields(current))
    f["_swapped"] = sorted(tried | ({old} if old else set()))
    store.q("update outreach_queue set fields = %s where id = %s", (Jsonb(f), rid))
    return row(store, rid)


def _mark_contacted(store, r: dict, campaign_id: int) -> None:
    store.q("update agencies set stage = 'contacted' where id = %s and stage in ('new', 'researching')", (r["agency_id"],))
    store.add_activity({"agency_id": r["agency_id"], "contact_id": r["contact_id"], "type": "email",
                        "notes": f"cold email queued (Smartlead campaign {campaign_id})"})
    city = (r["fields"].get("city") or "").lower()
    for k in r["fields"].get("_lead_keys") or []:
        store.q("insert into lead_assignments (hot_lead_key, agency_id, city, date) values (%s, %s, %s, %s) on conflict do nothing",
                (k, r["agency_id"], city, r["date"]))


def _push_one(sl, campaign_id: int, fields: dict, email: str, allow_other: bool = False) -> tuple[bool, str, int | None]:
    try:
        res = sl.add_lead(campaign_id, lead_payload(fields, email), allow_other_campaigns=allow_other)
    except Exception as exc:
        return False, str(exc)[:300], None
    added = res.get("added_count", res.get("upload_count"))
    ids = res.get("lead_ids") or []
    skipped = res.get("skipped_leads") or res.get("skipped") or []
    if (added is not None and int(added) == 0) or (skipped and not ids):
        reason = skipped[0] if skipped else res.get("message") or "Smartlead skipped the lead"
        return False, f"skipped by Smartlead: {reason}"[:300], None
    return True, "", (int(ids[0]) if ids else None)


def push(store, sl, row_ids: list[int]) -> list[dict]:
    """Push approved rows. Blocked, removed and already-pushed rows are never sent."""
    cids = campaign_ids(store)
    out = []
    for rid in row_ids:
        r = row(store, rid)
        if r is None or r["status"] not in ("built", "approved", "failed") or r["is_test"]:
            out.append({"id": rid, "ok": False, "error": "not pushable (blocked, removed or already pushed)"})
            continue
        cid = cids.get(r["campaign_role"])
        if not cid:
            out.append({"id": rid, "ok": False, "error": f"no {ROLE_LABEL.get(r['campaign_role'], r['campaign_role']).lower()} campaign set in Settings → Outreach"})
            continue
        ok, err, lead_id = _push_one(sl, cid, r["fields"], r["fields"]["_email"])
        if ok:
            store.q("""update outreach_queue set status = 'pushed', smartlead_lead_id = %s, smartlead_campaign_id = %s,
                       pushed_at = now(), error = null where id = %s""", (lead_id, cid, rid))
            _mark_contacted(store, r, cid)
        else:
            store.q("update outreach_queue set status = 'failed', error = %s, smartlead_campaign_id = %s where id = %s", (err, cid, rid))
        out.append({"id": rid, "ok": ok, "error": err or None, "smartlead_lead_id": lead_id})
    return out


def test_push(store, settings, sl, rid: int | None = None) -> dict:
    """Exactly one lead, to OWNER_TEST_EMAIL, with a real row's variables. Touches no agency."""
    if not settings.owner_test_email:
        raise ValueError("OWNER_TEST_EMAIL is not set on the server")
    r = row(store, rid) if rid else store.one("""select * from outreach_queue where date = %s and status in ('built', 'approved')
                                                  and not is_test order by id limit 1""", (uk_today(),))
    if r is None:
        raise ValueError("build the queue first: the test uses a real row's variables")
    cid = campaign_ids(store).get(r["campaign_role"])
    if not cid:
        raise ValueError(f"no {ROLE_LABEL.get(r['campaign_role'], r['campaign_role']).lower()} campaign set in Settings → Outreach")
    fields = {**r["fields"], "_email": settings.owner_test_email}
    ok, err, lead_id = _push_one(sl, cid, fields, settings.owner_test_email, allow_other=True)
    store.q("""insert into outreach_queue (date, agency_id, contact_id, campaign_role, fields, status, smartlead_lead_id,
               smartlead_campaign_id, error, is_test, pushed_at) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, true, now())""",
            (uk_today(), r["agency_id"], r["contact_id"], r["campaign_role"], Jsonb(fields), "pushed" if ok else "failed",
             lead_id, cid, err or None))
    return {"ok": ok, "error": err or None, "email": settings.owner_test_email, "campaign_id": cid, "from_row": r["id"],
            "fields": {k: v for k, v in fields.items() if not k.startswith("_")}}


def auto_push(store, settings, sl) -> dict:
    if not setting(store, S_AUTO, False):
        return {"skipped": "auto-push is off"}
    rows = build_queue(store, settings, sl)
    ids = [r["id"] for r in rows if r["status"] == "built"]
    res = push(store, sl, ids)
    return {"pushed": sum(1 for x in res if x["ok"]), "failed": sum(1 for x in res if not x["ok"])}


# ---------------------------------------------------------------------------
# replies
# ---------------------------------------------------------------------------
def sync_replies(store, sl, *, max_pages: int = 10, now: dt.datetime | None = None) -> dict:
    now = now or dt.datetime.now(dt.timezone.utc)
    last = (store.cache_get("outreach:replies_synced") or {}).get("at")
    since = dt.datetime.fromisoformat(last) - dt.timedelta(hours=1) if last else now - dt.timedelta(days=14)
    cids = [c for c in campaign_ids(store).values() if c]
    new = 0
    for page in range(max_pages):
        d = sl.inbox_replies(offset=page * 20, limit=20, campaign_ids=cids or None,
                             since_iso=since.strftime("%Y-%m-%dT%H:%M:%SZ"), until_iso=now.strftime("%Y-%m-%dT%H:%M:%SZ"))
        msgs = d.get("messages") or d.get("data") or []
        for m in msgs:
            last_msg = m.get("last_message") or {}
            mid = str(last_msg.get("id") or m.get("id") or "")
            email = ((m.get("lead") or {}).get("email") or last_msg.get("sent_from") or "").strip().lower()
            if not mid or not email:
                continue
            c = store.one("select id, agency_id from contacts where lower(email) = %s order by id limit 1", (email,))
            r = store.q("""insert into outreach_replies (smartlead_message_id, agency_id, contact_id, lead_email, smartlead_campaign_id,
                             campaign_lead_map_id, received_at, subject, snippet, smartlead_category)
                           values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) on conflict (smartlead_message_id) do nothing returning id""",
                        (mid, (c or {}).get("agency_id"), (c or {}).get("id"), email, (m.get("campaign") or {}).get("id"),
                         m.get("campaign_lead_map_id"), last_msg.get("received_at"), last_msg.get("subject"),
                         _text(last_msg.get("body") or "")[:400], (m.get("category") or {}).get("name")))
            new += len(r)
        if len(msgs) < 20:
            break
    store.cache_set("outreach:replies_synced", {"at": now.isoformat()})
    return {"new": new, "since": since.isoformat()}


def replies(store, limit: int = 200) -> list[dict]:
    return store.q("""select r.*, a.name as agency_name, c.full_name as contact_name from outreach_replies r
                      left join agencies a on a.id = r.agency_id left join contacts c on c.id = r.contact_id
                      order by r.received_at desc nulls last, r.id desc limit %s""", (limit,))


def categorise(store, sl, reply_id: int, category: str, today: dt.date | None = None) -> dict:
    if category not in CATEGORIES:
        raise ValueError(f"category must be one of {CATEGORIES}")
    today = today or uk_today()
    r = store.one("select * from outreach_replies where id = %s", (reply_id,))
    if r is None:
        raise ValueError("reply not found")
    aid, cid, email = r["agency_id"], r["contact_id"], r["lead_email"]
    done: list[str] = []
    if category == "interested" and aid:
        store.q("update agencies set stage = 'replied', next_follow_up = %s where id = %s", (today + dt.timedelta(days=2), aid))
        store.add_activity({"agency_id": aid, "contact_id": cid, "type": "note",
                            "notes": f"Task: Send 20 leads (due {today + dt.timedelta(days=2):%a %d %b})"})
        done.append("stage replied, follow-up task 'Send 20 leads' due in 48h")
    elif category == "not_now" and aid:
        store.q("update agencies set next_follow_up = %s where id = %s", (today + dt.timedelta(days=90), aid))
        done.append("follow up in 90 days")
    elif category in ("not_interested", "unsubscribe"):
        if cid:
            store.q("update contacts set do_not_contact = true where id = %s", (cid,))
        if aid:
            store.q("update agencies set stage = 'do_not_contact' where id = %s", (aid,))
        if email:
            store.suppress(email, category)
        done.append("do not contact + suppression list")
    elif category == "wrong_person" and cid:
        store.q("update contacts set do_not_contact = true where id = %s", (cid,))
        done.append("this contact won't be emailed again (agency stays)")
    smartlead: list[str] = []
    if sl is not None and email:
        try:
            lead = sl.lead_by_email(email) or {}
            lead_id = lead.get("id")
            cat = None
            from .smartlead import CATEGORY_IDS
            cat = CATEGORY_IDS.get(category)
            if lead_id and cat and r["smartlead_campaign_id"]:
                sl.set_category(int(r["smartlead_campaign_id"]), int(lead_id), cat, pause=category in ("not_interested", "unsubscribe"))
                smartlead.append("category set in Smartlead")
            if category in ("not_interested", "unsubscribe"):
                if lead_id:
                    sl.unsubscribe_everywhere(int(lead_id))
                    smartlead.append("unsubscribed from all Smartlead campaigns")
                sl.block([email])
                smartlead.append("added to Smartlead's block list")
        except Exception as exc:
            smartlead.append(f"Smartlead update failed: {exc}"[:200])
    if sl is not None and aid and category in COLLEAGUE_STOP:
        smartlead += _stop_colleagues(store, sl, aid, email, category)
    store.q("update outreach_replies set category = %s, categorised_at = now() where id = %s", (category, reply_id))
    return {"id": reply_id, "category": category, "crm": done, "smartlead": smartlead}


# A reply from one person at an agency stops the emails to their colleague in the other campaign. Smartlead's
# API has no documented "pause this lead" call, so this uses the category endpoint with pause_lead (the agency
# as a whole is interested / not interested). "Not now" has no Smartlead category: it's left to you.
COLLEAGUE_STOP = ("interested", "not_interested", "unsubscribe")


def _stop_colleagues(store, sl, agency_id: int, email: str | None, category: str) -> list[str]:
    from .smartlead import CATEGORY_IDS
    out = []
    rows = store.q("""select id, fields->>'_email' as email, smartlead_lead_id, smartlead_campaign_id from outreach_queue
                      where agency_id = %s and status = 'pushed' and not is_test and smartlead_lead_id is not null
                        and lower(fields->>'_email') <> lower(%s)""", (agency_id, email or ""))
    for r in rows:
        try:
            sl.set_category(int(r["smartlead_campaign_id"]), int(r["smartlead_lead_id"]), CATEGORY_IDS[category], pause=True)
            out.append(f"colleague {r['email']} paused in Smartlead")
        except Exception as exc:
            out.append(f"couldn't pause colleague {r['email']}: {exc}"[:200])
    return out


# ---------------------------------------------------------------------------
# health + stats
# ---------------------------------------------------------------------------
def health(store, sl, *, auto_pause: bool = True, today: dt.date | None = None) -> dict:
    today = today or uk_today()
    start = (today - dt.timedelta(days=30)).isoformat()
    accounts = sl.email_accounts()
    try:
        metrics = {m.get("email_account"): m for m in sl.mailbox_health(start, today.isoformat())}
    except Exception as exc:
        log.warning("mailbox health: %s", exc)
        metrics = {}
    inboxes = []
    for a in accounts:
        w = a.get("warmup_details") or {}
        m = metrics.get(a.get("from_email")) or {}
        sent, bounced = int(m.get("sent") or 0), int(m.get("bounced") or 0)
        rate = bounced / sent if sent else 0.0
        inboxes.append({"id": a.get("id"), "email": a.get("from_email"), "daily_limit": a.get("message_per_day"),
                        "sent_today": a.get("daily_sent_count"), "warmup_status": w.get("status"),
                        "reputation": w.get("warmup_reputation"), "sent_30d": sent, "bounced_30d": bounced,
                        "bounce_rate": round(rate, 4), "red": sent >= BOUNCE_MIN_SENDS and rate > BOUNCE_PAUSE,
                        "connected": bool(a.get("is_smtp_success")) and bool(a.get("is_imap_success"))})
    campaigns = []
    for role, cid in campaign_ids(store).items():
        if not cid:
            continue
        rows = sl.statistics(cid, limit=1000, since=(today - dt.timedelta(days=60)).isoformat())
        rows = sorted((x for x in rows if x.get("sent_time")), key=lambda x: x["sent_time"], reverse=True)[:BOUNCE_WINDOW]
        bounced = sum(1 for x in rows if x.get("is_bounced"))
        rate = bounced / len(rows) if rows else 0.0
        red = len(rows) >= BOUNCE_MIN_SENDS and rate > BOUNCE_PAUSE
        paused = False
        if red and auto_pause:
            flag = f"outreach:autopaused:{cid}"
            if store.cache_get(flag, max_age_days=1) is None:
                try:
                    sl.set_status(cid, "PAUSED")
                    paused = True
                    store.cache_set(flag, {"at": dt.datetime.now(dt.timezone.utc).isoformat(), "rate": rate})
                    log.warning("campaign %s auto-paused: %.1f%% bounces in the last %d sends", cid, rate * 100, len(rows))
                except Exception as exc:
                    log.warning("auto-pause of %s failed: %s", cid, exc)
        campaigns.append({"role": role, "campaign_id": cid, "last_sends": len(rows), "bounced": bounced,
                          "bounce_rate": round(rate, 4), "red": red, "auto_paused": paused})
    out = {"inboxes": inboxes, "campaigns": campaigns, "at": dt.datetime.now(dt.timezone.utc).isoformat()}
    store.cache_set("outreach:health", out)
    return out


def _sum_analytics(sl, cids: list[int], start: dt.date, end: dt.date) -> dict:
    tot = {"sent": 0, "replies": 0, "bounces": 0, "unsubscribes": 0}
    for cid in cids:
        a = sl.analytics_by_date(cid, start.isoformat(), end.isoformat())
        tot["sent"] += int(a.get("sent_count") or 0)
        tot["replies"] += int(a.get("non_ooo_reply_count") or a.get("reply_count") or 0)
        tot["bounces"] += int(a.get("bounce_count") or 0)
        tot["unsubscribes"] += int(a.get("unsubscribed_count") or 0)
    return tot


def stats(store, sl, *, refresh: bool = False, today: dt.date | None = None) -> dict:
    cached = store.cache_get("outreach:stats", max_age_days=15 / 1440)
    if cached and not refresh:
        return cached
    today = today or uk_today()
    week_start = today - dt.timedelta(days=today.weekday())
    cids = [c for c in campaign_ids(store).values() if c]
    if not cids:
        cids = [int(c["id"]) for c in sl.campaigns() if c.get("status") in ("ACTIVE", "PAUSED")]
    out = {"at": dt.datetime.now(dt.timezone.utc).isoformat(), "campaign_ids": cids}
    for name, start in (("today", today), ("week", week_start)):
        t = _sum_analytics(sl, cids, start, today)
        t["queued"] = int(store.val("""select count(*) from outreach_queue where status = 'pushed' and not is_test
                                       and date between %s and %s""", (start, today)) or 0)
        t["positive_replies"] = int(store.val("""select count(*) from outreach_replies where (category = 'interested'
                                     or smartlead_category in ('Interested', 'Meeting Request', 'Information Request'))
                                     and (received_at at time zone 'Europe/London')::date between %s and %s""", (start, today)) or 0)
        t["bounce_rate"] = round(t["bounces"] / t["sent"], 4) if t["sent"] else 0.0
        t["reply_rate"] = round(t["replies"] / t["sent"], 4) if t["sent"] else 0.0
        out[name] = t
    store.cache_set("outreach:stats", out)
    return out


def campaigns(store, sl) -> list[dict]:
    cids = campaign_ids(store)
    role = {v: k for k, v in cids.items() if v}
    out = []
    today = uk_today()
    for c in sl.campaigns():
        cid = int(c["id"])
        try:
            inboxes = len(sl.campaign_email_accounts(cid))
        except Exception:
            inboxes = None
        a = {}
        if c.get("status") in ("ACTIVE", "PAUSED", "COMPLETED"):
            try:
                a = sl.analytics_by_date(cid, (today - dt.timedelta(days=29)).isoformat(), today.isoformat())
            except Exception:
                a = {}
        out.append({"id": cid, "name": c.get("name"), "status": c.get("status"), "role": role.get(cid),
                    "inboxes": inboxes, "daily_limit": c.get("max_leads_per_day"),
                    "sent_30d": int(a.get("sent_count") or 0), "replies_30d": int(a.get("reply_count") or 0),
                    "url": f"https://app.smartlead.ai/app/email-campaign/{cid}/analytics"})
    return out
