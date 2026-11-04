"""Does a hot lead's job title belong to the order's industry?

Job boards match keywords anywhere in the ad, so a "sales executive" search
also returns optical assistants and solicitors. Before a lead goes into a
sheet, Claude Haiku (the cheapest model) judges each title against the
industry, many titles per call. Verdicts are cached per industry + title, so
each title is only ever judged once (a few pence per thousand titles).

Without ANTHROPIC_API_KEY (or if the call fails) a strict keyword check is
used instead: every word of one of the industry's job titles must start a
word in the ad's title.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from .collect import title_matches
from .normalise import title_norm

log = logging.getLogger("fit")

MODEL = "claude-haiku-5-5"
BATCH = 80
CACHE_DAYS = 180

SCHEMA = {"type": "object", "properties": {"fits": {"type": "array", "items": {"type": "integer"}}},
          "required": ["fits"], "additionalProperties": False}


def _key(industry: str, title: str) -> str:
    h = hashlib.sha1(f"{industry.strip().lower()}|{title_norm(title)}".encode()).hexdigest()[:20]
    return f"fit:{h}"


def keyword_fit(title: str, keywords: list[str]) -> bool:
    return any(title_matches(k, title) for k in keywords)


def _ask(client, industry: str, keywords: list[str], titles: list[str]) -> set[int]:
    lines = "\n".join(f"{i}. {t}" for i, t in enumerate(titles))
    prompt = (f"A UK recruitment agency specialises in this industry: {industry}.\n"
              f"Typical roles they fill: {', '.join(keywords)}.\n\n"
              "Below are job ad titles from UK employers. Return the numbers of the titles that are roles this "
              "agency would actually recruit for (the job itself belongs to the industry, not just mentions it). "
              "Leave out roles from other fields, and leave out in-house recruiter jobs unless the industry is "
              "recruitment.\n\n" + lines)
    resp = client.messages.create(
        model=MODEL, max_tokens=2000, thinking={"type": "disabled"},
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
        messages=[{"role": "user", "content": prompt}])
    if resp.stop_reason == "refusal":
        raise RuntimeError("refused")
    text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
    return {int(i) for i in json.loads(text)["fits"] if 0 <= int(i) < len(titles)}


def filter_fitting(store, client, industry: str, keywords: list[str], leads: list[dict],
                   cached_only: bool = False) -> list[dict]:
    """The leads whose title fits the industry. cached_only: no API calls, unknown titles left out."""
    verdict: dict[str, bool] = {}
    unknown: list[str] = []
    for l in leads:
        t = l["title"]
        if t in verdict or t in unknown:
            continue
        c = store.cache_get(_key(industry, t), max_age_days=CACHE_DAYS)
        if c is not None:
            verdict[t] = bool(c.get("fit"))
        else:
            unknown.append(t)
    if unknown and not cached_only:
        if client is None:
            for t in unknown:
                verdict[t] = keyword_fit(t, keywords)   # not cached: Claude may judge it later
        else:
            for i in range(0, len(unknown), BATCH):
                part = unknown[i:i + BATCH]
                try:
                    ok = _ask(client, industry, keywords, part)
                except Exception as exc:
                    log.warning("fit check failed (%s): using keyword check for %d titles", type(exc).__name__, len(part))
                    for t in part:
                        verdict[t] = keyword_fit(t, keywords)
                    continue
                for j, t in enumerate(part):
                    verdict[t] = j in ok
                    store.cache_set(_key(industry, t), {"fit": j in ok, "title": t, "industry": industry})
            log.info("fit: judged %d new titles for %s", len(unknown), industry)
    return [l for l in leads if verdict.get(l["title"], False)]


def make_client(settings) -> Any:
    if not settings.anthropic_api_key:
        return None
    import anthropic
    return anthropic.Anthropic(api_key=settings.anthropic_api_key, max_retries=3)
