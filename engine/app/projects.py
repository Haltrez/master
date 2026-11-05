"""The project agent: paste a research brief ("build a hot-lead list for this prospect agency...")
and it does the job the way a careful researcher would, then hands back a sheet, an email and a note.

Claude (the assistant model) runs a long tool loop with the research tools. Every step is logged
for the CRM's live view; the conversation is stored append-only, so you can reply to a finished
project ("add Bolton", "drop Kier") and it carries on with everything it already knows.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
import logging
from typing import Any

log = logging.getLogger("projects")

MAX_STEPS = 90
FALLBACK_BETA = "server-side-fallback-2026-07-01"
RESULT_LIMIT = 12000

SYSTEM = """You are the research agent for SmartEmploy (owner: Roberts). SmartEmploy sells UK recruitment \
agencies lists of "hot leads": real employers (never agencies) who are hiring roles the agency fills, ideally \
roles they keep re-advertising or have had open a long time. You get a project brief and you do the research \
yourself with your tools, then submit the deliverables.

How you work:
1. Read the brief. Note the client, the briefs/roles, radiuses, title variations, sources, hard rules and the \
exact output columns asked for.
2. Find leads. Start with any employers the brief names (re-check they're live). Use fetch_page on employers' \
careers pages and follow job links on them; sitemap_dates and wayback give real "open since" evidence. Use \
search_jobs for the job boards' official APIs (direct employers; it also counts agency ads = market demand). \
Use web_search to discover employers' careers pages and public LinkedIn profile URLs (from results only).
3. Check every candidate: check_ads (Claude Haiku reads the ad: industry, direct employer, not a recruiter, real \
vacancy); companies_house (real, active, not a recruitment business: SIC 78xxx means agency); distance (inside the \
brief's radius); check_link (still live today). Drop anything that fails. Count agency ads separately.
4. Decision makers: companies_house officers (directors) for small firms; web_search for a public LinkedIn URL; \
find_email only when the brief allows emails (verified results only).
5. Submit once with submit_results: columns exactly as the brief asks, one row per lead, sorted as the brief asks, \
summary lines (e.g. market demand per role), the email if asked, and a frank note to Roberts.

Hard rules (always, even if the brief is silent):
- Never invent anything: dates, repost counts, contacts, emails, links. Unknown is written "unknown".
- No recruitment agency ads as leads. "Our client", "on behalf of", recruiter names, SIC 78xxx: exclude and count.
- Every row's link must be checked live today with check_link or fetch_page. If a board blocks automated checks, \
say so in the row's notes.
- Accuracy beats volume: a short honest list is the right answer when the market is thin. Explain why in the note.
- No personal emails. No scraping of LinkedIn or Indeed (the tools refuse anyway).

What goes to the client (the sheet is sent to the agency as it is):
- For lead lists, always add an "Agency-friendly?" column, just before Notes unless the brief places it: \
"Yes", "No: <their words, short>" or "Unknown". Check every employer's careers page and job pages (fetch_page \
returns agency_policy when the page talks about agencies, third-party recruiters, unsolicited CVs or in-house \
recruiters). Never hide a "No": it is the most useful thing to know before calling.
- Notes: one short line useful to the client (pay, project size, travel, team, contract length). No working notes: \
no tool names, no "Haiku", no "CAUTION", no Companies House numbers or SIC codes, no doubts about your own research \
(those go in the private note).
- LinkedIn: only a full profile URL copied exactly from a web_search result for the right person. If you can't, write \
"unknown". Never shorten or guess a URL.
- Weak fits and doubts are said openly in the row, e.g. Match "Related role (energy project, 6-month contract: weak \
fit)" or Location "Manchester (ad says Manchester; company mostly works in East Anglia: location unconfirmed)".
- header_lines go above the table, for what the client must read first: employers that say no agencies (and why \
they may still be worth an approach), briefs with no direct employers and who is running those roles instead, \
and what you'll keep watching for. Plain, honest, short: the client will respect it.
- summary_lines go under the table (e.g. market demand per role, naming the agencies, including the client's own ads).
- Be economical: don't repeat the same search; batch ad ids into check_ads; keep going until the brief is answered \
or you have searched every sensible variation, then submit. You have about 80 tool steps."""


def _obj(props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": props, "required": required, "additionalProperties": False}


S, I, B = {"type": "string"}, {"type": "integer"}, {"type": "boolean"}
SA = {"type": "array", "items": S}

TOOLS: list[dict[str, Any]] = [
    {"name": "search_jobs", "strict": True,
     "description": "Search Reed and Adzuna (official APIs) for ads whose job title matches any of `titles`, around a "
                    "town or region. Returns direct-employer ads (id, company, title, location, posted date, link, "
                    "snippet) and the number of agency ads for the same titles (with agency names).",
     "input_schema": _obj({"titles": SA, "location": S, "radius_miles": I, "max_days_old": I,
                           "must_mention": {"type": "array", "items": S,
                                            "description": "Optional words one of which must appear in the title or ad text, e.g. electrical, M&E"}},
                          ["titles", "location"])},
    {"name": "check_ads", "strict": True,
     "description": "Claude Haiku reads ads (by id from search_jobs) and answers: industry_fit, direct_employer, "
                    "not_recruitment_firm, real_vacancy, agency_would_fill, with the clean role name and a reason. "
                    "Pass an industry preset name or describe the niche in include/exclude.",
     "input_schema": _obj({"ad_ids": SA, "industry": S, "include": S, "exclude": S,
                           "extra_ads": {"type": "array", "items": _obj({"id": S, "company": S, "title": S, "location": S,
                                                                          "description": S}, ["id", "company", "title", "description"]),
                                         "description": "Ads you found yourself (e.g. on a careers page) to check too"}},
                          ["ad_ids", "industry"])},
    {"name": "fetch_page", "strict": True,
     "description": "Fetch a page from an employer's own website (careers page, job page, .docx job advert) politely "
                    "(robots.txt, slow). Returns title, text, job-related links, Last-Modified, schema.org dates and "
                    "whether it says the vacancy is closed. Job boards and LinkedIn are refused.",
     "input_schema": _obj({"url": S}, ["url"])},
    {"name": "sitemap_dates", "strict": True,
     "description": "Last-changed dates of a site's pages from its sitemap. `contains` filters URLs (e.g. 'job').",
     "input_schema": _obj({"site": S, "contains": S}, ["site"])},
    {"name": "wayback", "strict": True,
     "description": "Internet Archive captures of a URL: count, first and last capture with archive links. "
                    "prefix=true covers every page under that path (e.g. a careers section).",
     "input_schema": _obj({"url": S, "prefix": B}, ["url"])},
    {"name": "check_link", "strict": True,
     "description": "Is an ad or careers page still live today?",
     "input_schema": _obj({"url": S}, ["url"])},
    {"name": "companies_house", "strict": True,
     "description": "Companies House: search by name (top results) or look up a company number (status, SIC codes, "
                    "whether it's a recruitment business, address, current officers).",
     "input_schema": _obj({"name": S, "number": S}, [])},
    {"name": "web_search", "strict": True,
     "description": "Web search (UK). For finding employers' careers pages and public LinkedIn profile URLs.",
     "input_schema": _obj({"query": S}, ["query"])},
    {"name": "distance", "strict": True,
     "description": "Straight-line miles between two UK places (towns or postcodes).",
     "input_schema": _obj({"from_place": S, "to_place": S}, ["from_place", "to_place"])},
    {"name": "find_email", "strict": True,
     "description": "Hunter.io (monthly cap): with first+last name, that person's verified business email; with only a "
                    "domain, hiring/HR people there. Only verified emails may be used.",
     "input_schema": _obj({"domain": S, "first_name": S, "last_name": S}, ["domain"])},
    {"name": "submit_results", "strict": True,
     "description": "Submit the finished deliverables (call once, at the end; call again only if Roberts asks for changes).",
     "input_schema": _obj({
         "columns": SA,
         "rows": {"type": "array", "items": SA, "description": "One row per lead, values in column order"},
         "header_lines": {"type": "array", "items": S, "description": "What the client must read first, shown above the table"},
         "summary_lines": {"type": "array", "items": S, "description": "Lines for under the table, e.g. market demand per role"},
         "email_subject": S, "email_body": S,
         "note": {"type": "string", "description": "Frank note to Roberts: thin briefs, least-sure leads, what to check by hand"}},
         ["columns", "rows", "header_lines", "summary_lines", "note"])},
]


# ---------------------------------------------------------------------------
# storage
# ---------------------------------------------------------------------------
def create(store, brief: str, title: str | None = None) -> dict:
    brief = brief.strip()
    if len(brief) < 20:
        raise ValueError("the brief is too short: paste the full project brief")
    title = (title or brief.split("\n", 1)[0])[:120]
    return store.one("insert into projects (title, brief) values (%s, %s) returning *", (title, brief))


def get(store, pid: int) -> dict | None:
    return store.one("select * from projects where id = %s", (pid,))


def list_all(store) -> list[dict]:
    return store.q("""select id, title, status, steps, error, created_at, finished_at, input_tokens, output_tokens,
                             (result is not null) as has_result, log -> -1 as last_step
                      from projects order by id desc limit 100""")


def _log(store, pid: int, text: str) -> None:
    store.q("update projects set log = log || %s::jsonb, steps = steps + 1 where id = %s",
            (json.dumps([{"at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "text": text[:300]}]), pid))


def result_csv(result: dict) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    cols = result.get("columns") or []
    for line in result.get("header_lines") or []:
        w.writerow([line])
    if result.get("header_lines"):
        w.writerow([])
    w.writerow(cols)
    for r in result.get("rows") or []:
        w.writerow((list(r) + [""] * len(cols))[:max(len(cols), len(r))])
    if result.get("summary_lines"):
        w.writerow([])
        for line in result["summary_lines"]:
            w.writerow([line])
    return buf.getvalue()


# ---------------------------------------------------------------------------
# the loop
# ---------------------------------------------------------------------------
def _describe(name: str, a: dict) -> str:
    if name == "search_jobs":
        return f"Searching job boards: {', '.join(a.get('titles', [])[:4])} around {a.get('location')}"
    if name == "check_ads":
        return f"Claude checking {len(a.get('ad_ids', [])) + len(a.get('extra_ads') or [])} ads ({a.get('industry')})"
    if name == "fetch_page":
        return f"Reading {a.get('url')}"
    if name == "sitemap_dates":
        return f"Sitemap dates for {a.get('site')}"
    if name == "wayback":
        return f"Web archive history of {a.get('url')}"
    if name == "check_link":
        return f"Checking link {a.get('url')}"
    if name == "companies_house":
        return f"Companies House: {a.get('number') or a.get('name')}"
    if name == "web_search":
        return f"Web search: {a.get('query')}"
    if name == "distance":
        return f"Distance {a.get('from_place')} to {a.get('to_place')}"
    if name == "find_email":
        return f"Email lookup at {a.get('domain')}"
    if name == "submit_results":
        return f"Submitted {len(a.get('rows', []))} rows"
    return name


def _clean_linkedin(research, rows: list[list[str]]) -> tuple[list[list[str]], int]:
    """A LinkedIn link must be one a search actually returned, whole. Anything else becomes "unknown"."""
    seen = getattr(research, "seen_urls", None)
    if seen is None:
        return rows, 0
    fixed, out = 0, []
    for r in rows:
        new = []
        for v in r:
            if isinstance(v, str) and "linkedin.com/" in v and v.strip().rstrip("/") not in seen:
                new.append("unknown"); fixed += 1
            else:
                new.append(v)
        out.append(new)
    return out, fixed


def _run_tool(research, store, pid: int, name: str, a: dict) -> Any:
    if name == "submit_results":
        rows, fixed = _clean_linkedin(research, a.get("rows") or [])
        a = {**a, "rows": rows}
        result = {k: a.get(k) for k in ("columns", "rows", "header_lines", "summary_lines", "email_subject", "email_body", "note")}
        store.q("update projects set result = %s where id = %s", (json.dumps(result), pid))
        return {"ok": True, "rows": len(a.get("rows") or []),
                **({"linkedin_links_removed": fixed, "why": "not found whole in a search result"} if fixed else {})}
    fn = getattr(research, name, None)
    if fn is None:
        return {"error": f"unknown tool {name}"}
    return fn(**{k: v for k, v in a.items() if v is not None})


def _dump(v: Any) -> str:
    t = json.dumps(v, default=str)
    return t if len(t) <= RESULT_LIMIT else t[:RESULT_LIMIT] + '..."(cut: ask for less at once)"'


def _call(client, **kw):
    """Long outputs must stream (the SDK refuses a non-streaming request that may run over 10 minutes)."""
    stream = getattr(client.beta.messages, "stream", None)
    if stream is None:          # test doubles
        return client.beta.messages.create(**kw)
    with stream(**kw) as s:
        return s.get_final_message()


def run(store, client, model: str, research, pid: int, message: str | None = None) -> dict:
    """Run (or continue, with a follow-up message) a project until it submits or runs out of steps."""
    from .assistant import _append, _block_dict, load
    p = get(store, pid)
    cid = f"project:{pid}"
    messages = load(store, cid)
    first = not messages
    text = (f"Project brief:\n\n{p['brief']}\n\nToday is {dt.date.today():%A %d %B %Y}." if first
            else (message or "Carry on."))
    user = {"role": "user", "content": [{"type": "text", "text": text}]}
    messages.append(user)
    _append(store, cid, user)
    store.q("update projects set status = 'running', error = null where id = %s", (pid,))
    _log(store, pid, "Started" if first else f"Follow-up: {text[:200]}")
    submitted = False
    try:
        for step in range(MAX_STEPS + 3):
            if (get(store, pid) or {}).get("status") == "cancelled":
                _log(store, pid, "Cancelled")
                return {"status": "cancelled"}
            resp = _call(client, model=model, max_tokens=32000, system=SYSTEM, tools=TOOLS, messages=messages,
                         output_config={"effort": "high"}, betas=[FALLBACK_BETA], fallbacks="default",
                         cache_control={"type": "ephemeral"})
            u = getattr(resp, "usage", None)
            if u is not None:
                store.q("update projects set input_tokens = input_tokens + %s, output_tokens = output_tokens + %s where id = %s",
                        (int((u.input_tokens or 0) + (getattr(u, "cache_read_input_tokens", 0) or 0)
                             + (getattr(u, "cache_creation_input_tokens", 0) or 0)), int(u.output_tokens or 0), pid))
            msg = {"role": "assistant", "content": [_block_dict(b) for b in resp.content]}
            messages.append(msg)
            _append(store, cid, msg)
            if resp.stop_reason == "refusal":
                raise RuntimeError("Claude declined this brief")
            if resp.stop_reason == "pause_turn":
                continue
            uses = [b for b in resp.content if getattr(b, "type", None) == "tool_use"]
            said = "\n".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()
            if said:
                _log(store, pid, said[:300])
            if not uses:
                break
            results = []
            for tu in uses:
                a = dict(tu.input or {})
                _log(store, pid, _describe(tu.name, a))
                if resp.stop_reason == "max_tokens":
                    out, err = {"error": "your message was cut off; send smaller tool calls"}, True
                else:
                    try:
                        out, err = _run_tool(research, store, pid, tu.name, a), False
                        if tu.name == "submit_results":
                            submitted = True
                    except Exception as exc:
                        log.warning("project %s tool %s failed: %s", pid, tu.name, exc)
                        out, err = {"error": str(exc)[:300] or type(exc).__name__}, True
                results.append({"type": "tool_result", "tool_use_id": tu.id, "content": _dump(out),
                                **({"is_error": True} if err else {})})
            if step == MAX_STEPS - 1 and not submitted:
                results.append({"type": "text", "text": "Step budget reached: verify nothing new, submit_results now with "
                                                        "what you have and say in the note what's unfinished."})
            tr = {"role": "user", "content": results}
            messages.append(tr)
            _append(store, cid, tr)
        has = (get(store, pid) or {}).get("result") is not None
        store.q("update projects set status = %s, finished_at = now(), error = %s where id = %s",
                ("done" if has else "error", None if has else "finished without submitting results", pid))
        _log(store, pid, "Finished" if has else "Finished without results")
        return {"status": "done" if has else "error"}
    except Exception as exc:
        log.exception("project %s failed", pid)
        if messages and messages[-1]["role"] == "user":
            _append(store, cid, {"role": "assistant", "content": [{"type": "text", "text": f"(stopped: {exc})"}]})
        store.q("update projects set status = 'error', error = %s, finished_at = now() where id = %s", (str(exc)[:500], pid))
        _log(store, pid, f"Stopped: {exc}"[:300])
        return {"status": "error", "error": str(exc)}
