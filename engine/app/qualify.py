"""Claude Haiku reads each candidate ad and gives yes/no signals before a lead can go out.

For every ad it answers four questions against the order's industry definition:
  industry_fit     the job itself belongs to the industry (not just mentioned)
  direct_employer  the company hiring is the one advertising (not a recruitment agency / job board)
  real_vacancy     a real, specific job (not a talent pool, franchise, commission-only/self-employed,
                   training course, volunteer or "register your interest" ad)
  agency_would_fill  a specialist agency could realistically get paid to fill it (not apprenticeships,
                   graduate schemes run in-house, or mass seasonal hiring)
A lead goes out only with four yeses. Haiku also gives the clean role name and a short reason,
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
SIGNALS = ("industry_fit", "direct_employer", "real_vacancy", "agency_would_fill")

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
- real_vacancy: a specific, real job with an employer. False for talent pools, "register your interest", \
franchise or business opportunities, self-employed or commission-only roles, multi-level marketing, training \
courses you pay for, volunteer roles.
- agency_would_fill: a specialist recruitment agency could realistically be paid a fee to fill it. False for \
apprenticeships, internships, in-house graduate schemes and mass seasonal/temporary volume hiring.
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


def _key(industry: str, ad_id: Any) -> str:
    return "qual:" + hashlib.sha1(f"{industry.strip().lower()}|{ad_id}".encode()).hexdigest()[:24]


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
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
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
        c = store.cache_get(_key(industry["name"], ad["id"]), max_age_days=CACHE_DAYS)
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
            store.cache_set(_key(industry["name"], ad["id"]), v.as_dict())
    passed = sum(1 for a in todo if a["id"] in out and out[a["id"]].ok)
    log.info("haiku: %s judged %d ads, %d passed", industry["name"], len(todo), passed)
    return out


def make_client(settings) -> Any:
    if not settings.anthropic_api_key:
        return None
    import anthropic
    return anthropic.Anthropic(api_key=settings.anthropic_api_key, max_retries=3)
