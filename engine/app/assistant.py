"""The CRM's AI assistant: type what you need ("20 IT leads in London for
Acme Recruitment, send to jo@acme.co.uk") and it runs the CRM for you.

A manual tool-use loop on the Claude API. Conversations are stored exactly as
sent (append-only, thinking blocks included) in assistant_messages.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import uuid
from dataclasses import dataclass
from typing import Any, Callable

from . import orders as O

log = logging.getLogger("assistant")

MAX_STEPS = 12
FALLBACK_BETA = "server-side-fallback-2026-07-01"
# When the agent's instructions or tools change between turns (an update), Claude's earlier thinking notes no
# longer match: drop them instead of failing. What it found and did (text, tool calls, results) is kept.
BINDING_BETA = "thinking-binding-controls-2026-08-01"
THINKING = {"type": "adaptive", "block_binding": {"prefix_mismatch_behavior": "drop_block"}}

SYSTEM = """You run the Repost Radar CRM for its owner (Roberts, SmartEmploy). The business sells UK \
recruitment agencies Google Sheets of "hot leads": real hiring companies (never agencies) that posted a vacancy, \
let it lapse and posted it again (2+ posts), so they are struggling to hire. Each industry + region is exclusive \
to one agency.

Two packages: "sheet" = a sheet of leads shared with the agency; "outreach" = the owner also contacts the \
companies and reports results back to the agency.

How you work:
- Do what is asked with the tools, straight away. Ask a short question only when a needed detail is missing \
(agency name, industry, region or number of leads). Defaults: once, package "sheet", radius 20 miles. "Every morning" / "daily" = frequency daily \
(up to leads_wanted new leads each morning, ongoing).
- An order runs in the background: collection takes about 5 minutes, then every candidate ad is read by Claude \
Haiku (right industry, direct employer, real vacancy, agency-fillable), checked against the town's radius and \
Companies House. Only leads passing everything go out, one row per company, so an order can start part-filled and \
top up every morning. Say so, and give the sheet link once there is one.
- To pitch an agency, use list_territories / preview_territory to say how many verified leads are ready; use \
watch_territory to start building history for an industry + town before it's sold.
- Before saying how many leads exist, check with the tools. Never invent leads, numbers, emails or links.
- Deleting an order cannot be undone: ask the owner to confirm (name the agency, industry and town) first.
- Industry: use a preset name from list_industries when one fits; otherwise pass custom keywords that are job \
titles (each word must appear in the job title).
- A pasted research brief (careers pages, web archive, specific roles, a sample for a prospect agency) goes to \
start_project straight away, with the brief exactly as pasted: tell the owner it's running and where to see it \
(the Projects tab).
- Replies are for a busy person on a phone: short, plain English, no markdown tables, no jargon."""


def _obj(props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": props, "required": required, "additionalProperties": False}


S, I, B = {"type": "string"}, {"type": "integer"}, {"type": "boolean"}

TOOLS: list[dict[str, Any]] = [
    {"name": "create_lead_order", "strict": True,
     "description": "Create a lead order for an agency: sets up the agency, its exclusive industry+region, collects "
                    "from every job board in the background and sends the leads to a new Google Sheet (shared with "
                    "client_email when given). Returns the order.",
     "input_schema": _obj({
         "agency_name": S, "industry": {"type": "string", "description": "Preset name from list_industries, or free text"},
         "location": {"type": "string", "description": "UK town/city/region, e.g. 'Leeds'"},
         "leads_wanted": I, "frequency": {"type": "string", "enum": ["once", "daily", "weekly"]},
         "package": {"type": "string", "enum": ["sheet", "outreach"]},
         "client_email": {"type": "string", "description": "Agency email to share the sheet with"},
         "keywords": {"type": "array", "items": S, "description": "Custom job-title keywords (overrides the preset)"},
         "radius_miles": I}, ["agency_name", "industry", "location", "leads_wanted"])},
    {"name": "list_orders", "strict": True,
     "description": "List lead orders with progress (sent / wanted, ready to send, status, sheet link).",
     "input_schema": _obj({"status": {"type": "string", "enum": ["collecting", "waiting", "done", "paused", "error"]}}, [])},
    {"name": "order_details", "strict": True,
     "description": "One order with the companies already sent to its sheet.",
     "input_schema": _obj({"order_id": I}, ["order_id"])},
    {"name": "send_more_now", "strict": True,
     "description": "Search again now and send whatever the order still owes (otherwise this happens every morning).",
     "input_schema": _obj({"order_id": I, "search_again": B}, ["order_id"])},
    {"name": "update_order", "strict": True,
     "description": "Change an order: number of leads, frequency, client email, or pause / resume it.",
     "input_schema": _obj({"order_id": I, "leads_wanted": I, "frequency": {"type": "string", "enum": ["once", "daily", "weekly"]},
                           "client_email": S, "paused": B}, ["order_id"])},
    {"name": "watch_territory", "strict": True,
     "description": "Start collecting an industry + town every morning before it's sold, so reposts build up and "
                    "an order for it later starts with history. Collection starts straight away.",
     "input_schema": _obj({"industry": S, "location": S, "radius_miles": I}, ["industry", "location"])},
    {"name": "list_territories", "strict": True,
     "description": "Every industry + town being collected: owner agency (if sold), ads seen, reposted roles found.",
     "input_schema": _obj({}, [])},
    {"name": "preview_territory", "strict": True,
     "description": "How many leads in a territory would pass every check today (Claude reads each ad, Companies "
                    "House), with a sample. Sends nothing. Takes up to a minute. Use before pitching an agency.",
     "input_schema": _obj({"search_id": I}, ["search_id"])},
    {"name": "start_project", "strict": True,
     "description": "Hand a research brief to the project agent (careers pages, job boards, web archive, Companies "
                    "House, link checks; returns a sheet, email and note in 10-30 minutes). Use it whenever the owner "
                    "pastes a brief like 'build a hot-lead list for this prospect agency'. Pass the brief exactly as given.",
     "input_schema": _obj({"brief": S}, ["brief"])},
    {"name": "delete_order", "strict": True,
     "description": "Delete an order and its sent-lead records, freeing its industry + region for another agency. "
                    "Only after the owner has clearly confirmed deleting that specific order.",
     "input_schema": _obj({"order_id": I}, ["order_id"])},
    {"name": "list_industries", "strict": True,
     "description": "Industry presets and the job titles each one searches for.",
     "input_schema": _obj({}, [])},
    {"name": "find_hot_leads", "strict": True,
     "description": "Search hot leads already found (all orders and searches) by job title words and/or location.",
     "input_schema": _obj({"title_contains": S, "location_contains": S, "verified_only": B, "limit": I}, [])},
    {"name": "agency_info", "strict": True,
     "description": "An agency's orders, leads delivered and outcomes reported (contacted / meeting / placement).",
     "input_schema": _obj({"agency_name": S}, ["agency_name"])},
    {"name": "record_outcome", "strict": True,
     "description": "Record what happened with a delivered lead (for the outreach package report).",
     "input_schema": _obj({"order_id": I, "company": {"type": "string", "description": "Company name as in the sheet"},
                           "outcome": {"type": "string", "enum": ["none", "contacted", "meeting", "placement"]}},
                          ["order_id", "company", "outcome"])},
    {"name": "system_status", "strict": True,
     "description": "Which job sources work (last run result), Google connection, lead counts, settings.",
     "input_schema": _obj({}, [])},
    {"name": "update_settings", "strict": True,
     "description": "Change lead rules: repost_threshold (posts needed, default 2), reposts_only (only ads that "
                    "lapsed and came back), verified_companies_only (Companies House checked companies only), "
                    "hunter_monthly_limit (Hunter.io email lookups allowed per month; free plan 25).",
     "input_schema": _obj({"repost_threshold": I, "reposts_only": B, "verified_companies_only": B,
                           "hunter_monthly_limit": I}, [])},
]


@dataclass
class Toolbox:
    store: Any
    settings: Any
    strict: bool
    new_order: Callable[[dict], dict]
    start_fill: Callable[..., Any]
    order_out: Callable[[dict], dict]
    google: Callable[[], dict]
    start_collect: Callable[[int], Any] | None = None
    start_project: Callable[[str], dict] | None = None
    tools_for: Callable[[], Any] | None = None

    def _order_brief(self, o: dict) -> dict:
        o = self.order_out(o)
        return {k: o.get(k) for k in ("id", "agency_name", "industry", "location", "leads_wanted", "frequency",
                                      "package", "client_email", "status", "note", "delivered_total",
                                      "delivered_period", "ready", "sheet_url")}

    def run(self, name: str, a: dict) -> Any:
        s = self.store
        if name == "create_lead_order":
            o = self.new_order({k: v for k, v in a.items() if v is not None})
            self.start_fill(o["id"], True)
            g = self.google()
            return {"order": self._order_brief(o), "google_connected": g["connected"],
                    "next": "collecting in the background; the sheet is created when the first verified leads are sent"
                    if g["connected"] else "Google isn't connected, so leads go to the order's CSV download"}
        if name == "list_orders":
            return [self._order_brief(o) for o in O.list_orders(s, a.get("status"))]
        if name == "order_details":
            o = O.get_order(s, a["order_id"])
            if not o:
                return {"error": "no such order"}
            sent = s.q("""select h.employer_name, h.title, h.location_name, h.posting_count_90d as times_posted,
                                 d.outcome, d.delivered_at::date as sent_on
                          from deliveries d join hot_leads h on h.lead_key = d.lead_key
                          where d.order_id = %s order by d.delivered_at desc limit 50""", (a["order_id"],))
            return {"order": self._order_brief(o), "sent": sent}
        if name == "send_more_now":
            o = O.get_order(s, a["order_id"])
            if not o:
                return {"error": "no such order"}
            if o["status"] in ("paused", "done"):
                return {"error": f"order is {o['status']}: resume it or raise leads_wanted first"}
            self.start_fill(o["id"], a.get("search_again", True) is not False)
            return {"ok": True, "note": "running in the background"}
        if name == "update_order":
            fields = {k: a[k] for k in ("leads_wanted", "frequency", "client_email") if a.get(k) is not None}
            if a.get("paused") is not None:
                fields["status"] = "paused" if a["paused"] else "waiting"
            o = O.get_order(s, a["order_id"])
            if not o:
                return {"error": "no such order"}
            o = O.update_order(s, a["order_id"], fields)
            if o["status"] == "done" and o["delivered_total"] < o["leads_wanted"]:
                s._update("lead_orders", o["id"], {"status": "waiting"})
                self.start_fill(o["id"], False)
            return self._order_brief(O.get_order(s, a["order_id"]))
        if name == "watch_territory":
            t = O.watch_territory(s, a["industry"], a["location"], a.get("radius_miles") or 20)
            if self.start_collect:
                self.start_collect(t["search_id"])
            return {"territory": t, "note": "collecting now and every morning"}
        if name == "list_territories":
            return O.territories(s)
        if name == "preview_territory":
            return O.preview_territory(s, a["search_id"], self.tools_for())
        if name == "start_project":
            if not self.start_project:
                return {"error": "projects aren't available here"}
            p = self.start_project(a["brief"])
            return {"project_id": p["id"], "link": f"/projects/{p['id']}", "note": "running in the background (10-30 minutes)"}
        if name == "delete_order":
            return {"deleted": O.delete_order(s, a["order_id"])}
        if name == "list_industries":
            return O.INDUSTRIES
        if name == "find_hot_leads":
            where, params = ["true"], []
            if a.get("title_contains"):
                where.append("h.title ilike %s"); params.append(f"%{a['title_contains']}%")
            if a.get("location_contains"):
                where.append("h.location_name ilike %s"); params.append(f"%{a['location_contains']}%")
            if a.get("verified_only", True):
                where.append("exists (select 1 from lead_contacts c where c.employer_norm = h.employer_norm "
                             "and c.verified_company)")
            params.append(min(int(a.get("limit") or 20), 50))
            rows = s.q(f"""select h.employer_name, h.title, h.location_name, h.posting_count_90d as times_posted,
                                  h.score, h.first_seen_at::date as first_seen, h.latest_url
                           from hot_leads h where {' and '.join(where)} and h.status <> 'ignored' and h.signal = 'active'
                           order by h.score desc limit %s""", params)
            total = s.val(f"select count(*) from hot_leads h where {' and '.join(where)} and h.status <> 'ignored' and h.signal = 'active'",
                          params[:-1])
            return {"total_matching": total, "top": rows}
        if name == "agency_info":
            ag = s.agency_by_name(a["agency_name"])
            if not ag:
                like = s.q("select name from agencies where name ilike %s limit 5", (f"%{a['agency_name']}%",))
                return {"error": "no agency with that name", "similar": [r["name"] for r in like]}
            orders = [self._order_brief(o) for o in O.list_orders(s) if o["agency_id"] == ag["id"]]
            outcomes = s.q("""select d.outcome, count(*) as n from deliveries d join clients c on c.id = d.client_id
                              where c.agency_id = %s group by d.outcome""", (ag["id"],))
            return {"agency": {k: ag.get(k) for k in ("id", "name", "website", "stage", "notes")},
                    "orders": orders, "outcomes": {r["outcome"]: r["n"] for r in outcomes}}
        if name == "record_outcome":
            r = s.q("""update deliveries d set outcome = %s from hot_leads h
                       where h.lead_key = d.lead_key and d.order_id = %s and h.employer_name ilike %s
                       returning h.employer_name, h.title""", (a["outcome"], a["order_id"], f"%{a['company']}%"))
            return {"updated": r} if r else {"error": "no delivered lead with that company on this order"}
        if name == "system_status":
            names = ("reed", "adzuna", "careerjet", "jooble", "indeed", "theirstack")
            sources = {n: s.cache_get(f"source_status:{n}") for n in names}
            counts = s.one("""select count(*) as hot_leads,
                                (select count(*) from lead_contacts where verified_company) as verified_companies,
                                (select count(*) from deliveries) as delivered from hot_leads""")
            keep = ("repost_threshold", "reposts_only", "verified_companies_only", "hunter_monthly_limit")
            return {"sources_last_run": sources, "google": self.google(), "counts": counts,
                    "settings": {k: s.setting(k) for k in keep}}
        if name == "update_settings":
            for k in ("repost_threshold", "reposts_only", "verified_companies_only", "hunter_monthly_limit"):
                if a.get(k) is not None:
                    s.set_setting(k, a[k])
            return {"ok": True, "note": "applies from the next collection run"}
        return {"error": f"unknown tool {name}"}


# ---------------------------------------------------------------------------
# conversation storage (append-only)
# ---------------------------------------------------------------------------
def load(store, cid: str) -> list[dict]:
    return [{"role": r["role"], "content": r["content"]}
            for r in store.q("select role, content from assistant_messages where conversation_id = %s order by seq", (cid,))]


def _append(store, cid: str, msg: dict) -> None:
    store.q("""insert into assistant_messages (conversation_id, seq, role, content)
               values (%s, coalesce((select max(seq) + 1 from assistant_messages where conversation_id = %s), 0), %s, %s)""",
            (cid, cid, msg["role"], json.dumps(msg["content"], default=str)))


def visible_history(store, cid: str) -> list[dict]:
    out = []
    for m in load(store, cid):
        text = "\n".join(b.get("text", "") for b in m["content"] if isinstance(b, dict) and b.get("type") == "text").strip()
        if text:
            out.append({"role": m["role"], "text": text})
    return out


def _block_dict(b: Any) -> dict:
    return b if isinstance(b, dict) else b.to_dict()


def _result_text(value: Any) -> str:
    text = json.dumps(value, default=lambda v: v.isoformat() if isinstance(v, (dt.date, dt.datetime)) else str(v))
    return text if len(text) <= 20000 else text[:20000] + "...(cut)"


def chat(store, client, model: str, tools: Toolbox, message: str, conversation_id: str | None = None) -> dict[str, Any]:
    cid = conversation_id or uuid.uuid4().hex
    messages = load(store, cid)
    user = {"role": "user", "content": [{"type": "text", "text": message.strip()}]}
    messages.append(user)
    _append(store, cid, user)
    actions: list[str] = []
    reply = ""
    try:
        for _ in range(MAX_STEPS):
            resp = client.beta.messages.create(
                model=model, max_tokens=16000, system=SYSTEM, tools=TOOLS, messages=messages,
                output_config={"effort": "medium"}, betas=[FALLBACK_BETA, BINDING_BETA], fallbacks="default",
                thinking=THINKING)
            msg = {"role": "assistant", "content": [_block_dict(b) for b in resp.content]}
            messages.append(msg)
            _append(store, cid, msg)
            if resp.stop_reason == "refusal":
                reply = "Sorry, I can't help with that one."
                break
            if resp.stop_reason == "pause_turn":
                continue
            uses = [b for b in resp.content if getattr(b, "type", None) == "tool_use"]
            reply = "\n".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()
            if not uses:
                break
            results = []
            for u in uses:
                if resp.stop_reason == "max_tokens":
                    out, err = {"error": "your reply was cut off; try again with a shorter request"}, True
                else:
                    try:
                        out, err = tools.run(u.name, dict(u.input or {})), False
                        actions.append(u.name)
                    except Exception as exc:
                        log.warning("assistant tool %s failed: %s", u.name, exc)
                        out, err = {"error": str(exc)[:500] or type(exc).__name__}, True
                results.append({"type": "tool_result", "tool_use_id": u.id, "content": _result_text(out),
                                **({"is_error": True} if err else {})})
            tr = {"role": "user", "content": results}
            messages.append(tr)
            _append(store, cid, tr)
        else:
            reply = reply or "That took too many steps; tell me what to do next."
    except Exception as exc:
        import anthropic
        if isinstance(exc, anthropic.APIStatusError):
            why = f"Claude API error {exc.status_code}"
            if exc.status_code == 401:
                why = "the ANTHROPIC_API_KEY on the server was refused"
        elif isinstance(exc, anthropic.APIConnectionError):
            why = "couldn't reach the Claude API"
        else:
            raise
        log.warning("assistant: %s", why)
        reply = f"Sorry, {why}. Try again in a minute."
        if messages[-1]["role"] == "user":   # keep user/assistant turns alternating in the stored history
            _append(store, cid, {"role": "assistant", "content": [{"type": "text", "text": reply}]})
    return {"conversation_id": cid, "reply": reply or "Done.", "actions": actions}
