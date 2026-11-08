"""Claude Haiku reads each candidate ad and gives yes/no signals before a lead can go out.

For every ad it answers five questions against the order's industry definition:
  industry_fit     the job itself belongs to the industry (not just mentioned)
  direct_employer  the company hiring is the one advertising (not a recruitment agency / job board)
  real_vacancy     a real, specific job (not a talent pool, franchise, commission-only/self-employed,
                   training course, volunteer or "register your interest" ad)
  agency_would_fill  a specialist agency could realistically get paid to fill it (not apprenticeships,
                   graduate schemes run in-house, or mass seasonal hiring)
  not_recruitment_firm  the company isn't itself a recruitment/staffing business (a competitor)
A lead goes out only with five yeses. Haiku also gives the clean role name and a short reason,
which go into the sheet.

Verdicts are cached per industry + ad, so an ad is read once. Ads are sent in small batches with
strict JSON output; anything unparseable or refused counts as "not judged" (nothing goes out, it's
retried next time). No API key -> a strict job-title keyword check stands in (marked as such).
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Any

from .collect import title_matches

log = logging.getLogger("qualify")

MODEL = "claude-haiku-5-5"
BATCH = 8
CACHE_DAYS = 120
SIGNALS = ("industry_fit", "direct_employer", "not_recruitment_firm", "real_vacancy", "agency_would_fill")

VERDICT = {"type": "object", "additionalProperties": False,
           "required": ["id", *SIGNALS, "role", "reason"],
           "properties": {"id": {"type": "integer"}, **{k: {"type": "boolean"} for k in SIGNALS},
                          "role": {"type": "string"}, "reason": {"type": "string"}}}
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["verdicts"],
          "properties": {"verdicts": {"type": "array", "items": VERDICT}}}

SYSTEM = """You screen UK job ads for a lead-generation service that sells recruitment agencies \
"hot leads": real companies that keep re-advertising a role they can't fill. The agency buying the leads \
specialises in one industry. A wrong lead wastes their time and costs us the client, so be strict: when in \
doubt, answer false.

For each ad answer:
- industry_fit: the job itself is clearly inside the industry's "in" list and not in its "out" list. Judge the \
actual duties, not words in the company blurb. A job that only mentions the industry in passing is false.
- direct_employer: the advertiser is the company that will employ the person. False when the ad is from a \
recruitment agency, staffing/umbrella company or job board advertising for a client ("our client", "on behalf \
of", "we are recruiting for a ..." about another company), or when the advertiser's main business is recruitment \
or outsourced fundraising/field-marketing teams.
- not_recruitment_firm: the hiring company itself is not a recruitment, staffing, executive-search, RPO, \
contractor-supply or umbrella business, even when it is hiring for its own office (a recruiter hiring its own sales \
staff is a competitor of our client, not a lead). Use what you know about the company and the ad text.
- real_vacancy: a specific, real job with an employer. False for talent pools, "register your interest", \
franchise or business opportunities, self-employed or commission-only roles, multi-level marketing, training \
courses you pay for, volunteer roles.
- agency_would_fill: a specialist recruitment agency could realistically be paid a fee to fill it. Permanent, \
fixed-term, contract, maternity-cover, part-time and entry-level jobs are all fine. False only for apprenticeships, \
internships, in-house graduate schemes and mass seasonal/temporary volume hiring.
- role: the job's clean name (e.g. "Field Sales Executive"), no salary or location.
- reason: at most 15 words, plain English, why it passes or the first reason it fails."""


@dataclass
class Verdict:
    ok: bool
    role: str
    reason: str
    signals: dict[str, bool]
    by: str            # "haiku" | "keywords"

    def as_dict(self) -> dict:
        return {"ok": self.ok, "role": self.role, "reason": self.reason, "signals": self.signals, "by": self.by}


def _key(industry: dict, ad_id: Any) -> str:
    """Verdicts are kept per industry definition: changing an industry's in/out text re-checks its ads once."""
    defn = f"{industry['name'].strip().lower()}|{industry.get('in', '')}|{industry.get('out', '')}"
    return "qual3:" + hashlib.sha1(f"{defn}|{ad_id}".encode()).hexdigest()[:24]


def _ad_block(i: int, ad: dict) -> str:
    desc = (ad.get("description") or "").strip() or "(no description available)"
    salary = ad.get("salary") or ""
    return (f"<ad id=\"{i}\">\nCompany: {ad['company']}\nTitle: {ad['title']}\nLocation: {ad.get('location') or ''}\n"
            f"{'Salary: ' + salary + chr(10) if salary else ''}Description: {desc[:2500]}\n</ad>")


def _ask(client, industry: dict, ads: list[dict]) -> dict[int, Verdict]:
    prompt = (f"Industry: {industry['name']}\nIn: {industry['in']}\nOut: {industry['out']}\n\n"
              + "\n\n".join(_ad_block(i, a) for i, a in enumerate(ads))
              + "\n\nReturn one verdict per ad id.")
    resp = client.messages.create(
        model=MODEL, max_tokens=8000, system=SYSTEM,
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}},
        messages=[{"role": "user", "content": prompt}])
    if resp.stop_reason in ("refusal", "max_tokens"):
        raise RuntimeError(f"haiku stopped: {resp.stop_reason}")
    text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
    out: dict[int, Verdict] = {}
    for v in json.loads(text)["verdicts"]:
        i = int(v["id"])
        if 0 <= i < len(ads):
            sig = {k: bool(v[k]) for k in SIGNALS}
            out[i] = Verdict(ok=all(sig.values()), role=(v.get("role") or ads[i]["title"]).strip(),
                             reason=(v.get("reason") or "").strip(), signals=sig, by="haiku")
    return out


def keyword_verdict(ad: dict, industry: dict) -> Verdict:
    ok = any(title_matches(k, ad["title"]) for k in industry["search"])
    return Verdict(ok=ok, role=ad["title"], reason="job title matches the industry" if ok else "job title doesn't match",
                   signals={"industry_fit": ok}, by="keywords")


def judge(store, client, industry: dict, ads: list[dict]) -> dict[Any, Verdict]:
    """ads: [{id, company, title, location, salary, description}] -> {id: Verdict} (missing = not judged)."""
    out: dict[Any, Verdict] = {}
    todo: list[dict] = []
    for ad in ads:
        c = store.cache_get(_key(industry, ad["id"]), max_age_days=CACHE_DAYS)
        if c is not None:
            out[ad["id"]] = Verdict(**c)
        else:
            todo.append(ad)
    if not todo:
        return out
    if client is None:
        for ad in todo:
            out[ad["id"]] = keyword_verdict(ad, industry)
        return out
    from concurrent.futures import ThreadPoolExecutor
    parts = [todo[i:i + BATCH] for i in range(0, len(todo), BATCH)]

    def run(part):
        try:
            return part, _ask(client, industry, part)
        except Exception as exc:
            log.warning("haiku check failed for %d ads (%s: %s); they wait for the next run",
                        len(part), type(exc).__name__, str(exc)[:200])
            return part, {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(run, parts))
    for part, got in results:
        for j, ad in enumerate(part):
            v = got.get(j)
            if v is None:
                continue
            out[ad["id"]] = v
            store.cache_set(_key(industry, ad["id"]), v.as_dict())
    passed = sum(1 for a in todo if a["id"] in out and out[a["id"]].ok)
    log.info("haiku: %s judged %d ads, %d passed", industry["name"], len(todo), passed)
    return out


def make_client(settings) -> Any:
    if not settings.anthropic_api_key:
        return None
    import anthropic
    return anthropic.Anthropic(api_key=settings.anthropic_api_key, max_retries=3)


PICK_SCHEMA = {"type": "object", "additionalProperties": False, "required": ["company_number", "why"],
               "properties": {"company_number": {"type": "string"}, "why": {"type": "string"}}}


def pick_company(client, employer: str, ad_text: str, items: list[dict]) -> str | None:
    """Which Companies House record is the employer behind this ad? Returns a company number or None.
    Used only when the name match alone isn't sure (brands vs registered names: "ASDA" -> ASDA STORES LIMITED)."""
    if client is None or not items:
        return None
    lines = "\n".join(
        f"- {it.get('company_number')}: {it.get('title')} | {it.get('company_status')} | {it.get('company_type')} | "
        f"{it.get('address_snippet') or ''} | created {it.get('date_of_creation') or '?'}" for it in items[:6])
    prompt = (f"A UK job ad is from the employer \"{employer}\". Ad excerpt:\n{(ad_text or '')[:1200]}\n\n"
              f"Companies House search results:\n{lines}\n\nWhich record is the company that employs people under "
              "that name (the main UK trading company of the brand, not a dormant, dissolved or unrelated namesake)? "
              "Answer with its company_number, or an empty string if none clearly is.")
    resp = client.messages.create(
        model=MODEL, max_tokens=1500, output_config={"effort": "low", "format": {"type": "json_schema", "schema": PICK_SCHEMA}},
        messages=[{"role": "user", "content": prompt}])
    if resp.stop_reason in ("refusal", "max_tokens"):
        return None
    text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
    number = (json.loads(text).get("company_number") or "").strip()
    return number if any(it.get("company_number") == number for it in items) else None
