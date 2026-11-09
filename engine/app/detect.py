"""Repost detection: which companies can't fill which roles.

A company that posts the same role again and again (the ad expires, they
post it again) hasn't found anyone. That's a hot lead for an agency.

For every direct-employer listing seen in the lookback window, grouped by
lead key (employer | title | town):

  posting_count_90d  distinct ads first seen in the window (per source, the
                     max across sources, so a Reed ad and its Adzuna copy
                     don't count twice)
  live_now           an ad for the key was seen in the latest run
  long_open          one ad open 30+ days (last seen minus the earlier of
                     first seen and the posted date Reed gives us)
  day_one            2+ ads for the key live at the same time

Hot if posting_count >= threshold, or long_open, or day_one.
Score = 2*(count-1) + 2 if long_open + 1 if the employer has 3+ hot keys + 1 if live.
"""

from __future__ import annotations

import datetime as dt
import re
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from .normalise import lead_key

log = logging.getLogger("detect")

LONG_OPEN_DAYS = 30
# Client feedback: a page that stays up unchanged is often an "evergreen" listing, not real demand.
ACTIVE_DAYS = 30          # ACTIVE: the role was advertised again (a new posting / new job URL) in the last 30 days
EVERGREEN_DAYS = 60       # PASSIVE: the same ad / page up unchanged for 60+ days
EVERGREEN_WORDS = re.compile(r"always looking|speculative|talent pool|keen to hear from", re.I)
LIVE_SLACK = dt.timedelta(hours=1)
# An ad only counts as "ended" once we've stopped seeing it for this long before the next one appears.
# (Within one run ads are seen seconds apart: that is the same ad in several places, not a repost.)
REPOST_GAP = dt.timedelta(days=2)
DEFAULT_LIVE_WINDOW = dt.timedelta(hours=36)
# Ads that opened within this many days of each other are one posting (copies in several places,
# or the same ad on several boards). A posting that opens later than this is the role advertised again.
EVENT_GAP = dt.timedelta(days=7)


@dataclass
class Listing:
    job_id: int
    source: str
    employer_name: str
    employer_norm: str
    title: str
    title_norm: str
    location_name: str | None
    location_norm: str | None
    first_seen_at: dt.datetime
    last_seen_at: dt.datetime
    posted_date: dt.date | None = None
    expiration_date: dt.date | None = None
    url: str | None = None
    search_ids: list[int] = field(default_factory=list)
    source_says_repost: bool = False
    description: str | None = None

    @property
    def key(self) -> str:
        return lead_key(self.employer_norm, self.title_norm, self.location_norm or "")

    def opened(self) -> dt.datetime:
        if self.posted_date:
            posted = dt.datetime.combine(self.posted_date, dt.time(), tzinfo=dt.timezone.utc)
            return min(self.first_seen_at, posted)
        return self.first_seen_at


def _is_live(l: Listing, latest_run: dict[int, dt.datetime], now: dt.datetime) -> bool:
    starts = [latest_run[s] for s in l.search_ids if s in latest_run]
    if starts:
        return l.last_seen_at >= min(starts) - LIVE_SLACK
    return l.last_seen_at >= now - DEFAULT_LIVE_WINDOW


def _reposted(ls: list[Listing]) -> bool:
    """An earlier ad ended before a later one started: posted, expired, posted again."""
    by_source: dict[str, list[Listing]] = defaultdict(list)
    for l in ls:
        by_source[l.source].append(l)
    for group in by_source.values():
        group.sort(key=lambda l: l.first_seen_at)
        earliest_end = None
        for l in group:
            if earliest_end is not None and earliest_end + REPOST_GAP <= l.first_seen_at:
                return True
            earliest_end = l.last_seen_at if earliest_end is None else min(earliest_end, l.last_seen_at)
    return False


_FILLER = {"and", "the", "of", "for", "to", "a", "an", "in", "with", "amp"}


def _place_words() -> frozenset[str]:
    from .normalise import POSTCODE_AREA_TOWN
    words = {w for town in POSTCODE_AREA_TOWN.values() for w in town.split()}
    words |= {"north", "south", "east", "west", "northern", "southern", "eastern", "western", "midlands", "central",
              "scotland", "wales", "ireland", "england", "uk", "national", "region", "regional", "yorkshire", "lancashire",
              "cheshire", "kent", "essex", "surrey", "sussex", "devon", "cornwall", "cumbria", "lincolnshire",
              "humber", "anglia", "home", "counties", "greater"}
    return frozenset(words - {"st", "the", "upon", "on", "le"})


PLACE_WORDS = _place_words()


def _tokens(title_norm: str) -> frozenset[str]:
    return frozenset(t for t in title_norm.split() if t not in _FILLER)


def canonical_titles(listings: list[Listing]) -> dict[tuple[str, str, str], str]:
    """Same company + town: a title that is another title plus extra words is the same role reworded
    ("sales executive" / "door to door sales executive"). Maps (employer, town, title) -> core title.
    The core needs 2+ words, so "manager" never swallows "store manager" and "sales manager"."""
    by_place: dict[tuple[str, str], set[str]] = defaultdict(set)
    for l in listings:
        by_place[(l.employer_norm, l.location_norm or "")].add(l.title_norm)
    out: dict[tuple[str, str, str], str] = {}
    for (emp, loc), titles in by_place.items():
        if len(titles) < 2:
            continue
        toks = {t: _tokens(t) for t in titles}
        for t in titles:
            # "area sales manager north east" is a different vacancy from "... cambridge": places never merge
            cores = [c for c in titles if c != t and len(toks[c]) >= 2 and toks[c] < toks[t]
                     and not ((toks[t] - toks[c]) & PLACE_WORDS)]
            if cores:
                out[(emp, loc, t)] = min(cores, key=lambda c: (len(toks[c]), c))
    return out


def posting_events(ls: list[Listing]) -> list[list[Listing]]:
    """Group a role's ads into distinct postings: a new posting starts when an ad opens 7+ days after the
    current posting started, or after every ad of the current posting was seen gone for 2+ days."""
    events: list[list[Listing]] = []
    for l in sorted(ls, key=lambda x: (x.opened(), x.first_seen_at, x.job_id)):
        if events:
            ev = events[-1]
            start = min(x.opened() for x in ev)
            ended = max(x.last_seen_at for x in ev)
            if l.opened() - start < EVENT_GAP and not (ended + REPOST_GAP <= l.first_seen_at):
                ev.append(l)
                continue
        events.append([l])
    return events


def compute_hot_leads(
    listings: list[Listing],
    latest_run: dict[int, dt.datetime],
    now: dt.datetime,
    threshold: int = 3,
    lookback_days: int = 90,
    archive: dict[str, dict] | None = None,
    reposts_only: bool = False,
) -> list[dict[str, Any]]:
    """archive: group key -> {"prior_count", "oldest_capture", "old_url"} from the Wayback Machine.
    reposts_only: hot only when the role was posted, ended and posted again (ours or archived)."""
    archive = archive or {}
    cutoff = now - dt.timedelta(days=lookback_days)
    recent_ls = [l for l in listings if l.last_seen_at >= cutoff]
    canon = canonical_titles(recent_ls)
    groups: dict[str, list[Listing]] = defaultdict(list)
    for l in recent_ls:
        loc = l.location_norm or ""
        core = canon.get((l.employer_norm, loc, l.title_norm))
        groups[lead_key(l.employer_norm, core, loc) if core else l.key].append(l)

    leads: list[dict[str, Any]] = []
    for key, ls in groups.items():
        recent = [l for l in ls if l.first_seen_at >= cutoff or l.opened() >= cutoff]
        events = posting_events(recent or ls)
        count = len(events)
        live = [l for l in ls if _is_live(l, latest_run, now)]
        live_by_source: dict[str, set[int]] = defaultdict(set)
        for l in live:
            live_by_source[l.source].add(l.job_id)
        day_one = any(len(v) >= 2 for v in live_by_source.values())
        long_open = any((l.last_seen_at - l.opened()).days >= LONG_OPEN_DAYS for l in ls)
        ev = max((archive.get(k) or {} for k in {key, *(x.key for x in ls)}),
                 key=lambda e: int(e.get("prior_count") or 0))
        prior = int(ev.get("prior_count") or 0)
        count += prior
        flagged = any(l.source_says_repost for l in ls)
        if flagged and count < 2:
            count = 2      # the board itself says this ad is a repost: at least two postings
        repost = len(events) >= 2 or _reposted(ls) or prior > 0 or flagged
        if reposts_only:
            if not (repost and count >= threshold):
                continue
        elif not (count >= threshold or long_open or day_one):
            continue
        latest = max(ls, key=lambda l: (l.opened(), l.first_seen_at, l.job_id))
        previous = events[0][0] if len(events) >= 2 else None
        sig = signals(events, ls, now, prior, flagged)
        leads.append({
            "lead_key": key,
            "employer_name": latest.employer_name,
            "employer_norm": latest.employer_norm,
            "title": latest.title,
            "title_norm": latest.title_norm,
            "location_name": latest.location_name,
            "location_norm": latest.location_norm,
            "posting_count_90d": max(count, 1),
            "first_seen_at": min([l.opened() for l in ls] + ([dt.datetime.combine(ev["oldest_capture"], dt.time(),
                                 tzinfo=dt.timezone.utc)] if ev.get("oldest_capture") else [])),
            "old_ad_url": ev.get("old_url"),
            "archive_prior": prior,
            "reposted": repost,
            "flagged_repost": flagged,
            "posting_dates": [min(x.opened() for x in ev).date() for ev in events],
            "previous_url": previous.url if previous else None,
            "last_seen_at": max(l.last_seen_at for l in ls),
            "live_now": bool(live),
            "long_open": long_open,
            "day_one": day_one,
            "latest_url": latest.url,
            "latest_source": latest.source,
            "latest_job_id": latest.job_id,
            "expiration_date": max((l.expiration_date for l in ls if l.expiration_date), default=None),
            "job_ids": sorted({l.job_id for l in ls}),
            "sources": sorted({l.source for l in ls}),
            "search_ids": sorted({s for l in ls for s in l.search_ids}),
            **sig,
        })

    hot_keys_per_employer: dict[str, int] = defaultdict(int)
    for lead in leads:
        hot_keys_per_employer[lead["employer_norm"]] += 1
    for lead in leads:
        lead["score"] = score(lead, hot_keys_per_employer[lead["employer_norm"]])
    leads.sort(key=lambda x: (-x["score"], x["employer_norm"], x["title_norm"]))
    return leads


def signals(events: list[list[Listing]], ls: list[Listing], now: dt.datetime, prior: int, flagged: bool) -> dict:
    """ACTIVE: a posting that opened in the last 30 days and is a re-post (an earlier posting exists: ours, an
    archived ad, or the board itself says repost). A new posting has a new job URL by construction (copies of
    one ad within 7 days are one posting).
    PASSIVE: only an ad / page that has stayed up unchanged. Evergreen: the same ad up 60+ days with no closing
    date, or worded like a standing advert ('always looking', 'talent pool'...). Evergreen wording makes a lead
    passive even when it keeps being reposted: a refreshed talent-pool ad isn't a vacancy."""
    cut = now - dt.timedelta(days=ACTIVE_DAYS)
    active_dates = []
    for i, ev in enumerate(events):
        start = min(x.opened() for x in ev)
        if start >= cut and (i > 0 or prior > 0 or any(x.source_says_repost for x in ev)):
            active_dates.append(start)
    unchanged = [l for l in ls if (l.last_seen_at - l.opened()).days >= EVERGREEN_DAYS]
    reason = None
    words = next((m.group(0) for l in ls for m in [EVERGREEN_WORDS.search(f"{l.title} {l.description or ''}")] if m), None)
    if words:
        reason = f'worded like a standing advert ("{words}")'
    elif any(l.expiration_date is None for l in unchanged):
        days = max((l.last_seen_at - l.opened()).days for l in unchanged)
        reason = f"same ad up unchanged for {days} days with no closing date"
    active = bool(active_dates) and not words
    return {"signal": "active" if active else "passive", "evergreen": reason is not None, "evergreen_reason": reason,
            "last_active_at": max(active_dates) if active_dates else None}


def score(lead: dict, roles_at_employer: int = 1) -> int:
    """ACTIVE re-post in the last 30 days +4, each further re-post in 90 days +2; passive-only leads score 0
    (labelled 'possibly evergreen' when they look it). Small tie-breakers for active leads only."""
    if lead.get("signal") != "active":
        return 0
    reposts = max(0, int(lead["posting_count_90d"] or 1) - 1)
    return 4 + 2 * max(0, reposts - 1) + (1 if roles_at_employer >= 3 else 0) + (1 if lead.get("live_now") else 0)


def check_archive(store, listings: list[Listing], archive, now: dt.datetime, max_checks: int = 150,
                  priority=None) -> int:
    """Wayback lookups for live company+role groups not checked in 30 days (best-effort, capped)."""
    live: dict[str, Listing] = {}
    for l in listings:
        if l.last_seen_at >= now - dt.timedelta(days=2):
            cur = live.get(l.key)
            if cur is None or l.first_seen_at < cur.first_seen_at:
                live[l.key] = l
    fresh = store.archive_checked_since(now - dt.timedelta(days=30))
    todo = [k for k in live if k not in fresh]
    if priority is not None:   # roles in territories with an open order, matching its industry, go first
        todo.sort(key=lambda k: -priority(live[k]))
    todo = todo[:max_checks]
    for k in todo:
        l = live[k]
        first = min(l.opened(), l.first_seen_at).date()
        ev = archive.prior_postings(l.employer_name, l.title, l.title_norm, before=first)
        store.save_archive_evidence(k, l.employer_norm, l.title_norm, ev)
    if todo:
        log.info("archive: checked %d company+role groups", len(todo))
    return len(todo)


def rebuild_hot_leads(store, *, threshold: int = 3, lookback_days: int = 90, reed=None,
                      max_detail_calls: int = 50, now: dt.datetime | None = None, archive=None,
                      max_archive_checks: int = 150, reposts_only: bool = False,
                      archive_priority=None) -> dict[str, int]:
    """Recompute hot_leads from the database. Keeps every status the owner set."""
    now = now or dt.datetime.now(dt.timezone.utc)
    rows = store.employer_listings_since(now - dt.timedelta(days=lookback_days))
    listings = [Listing(**r) for r in rows]
    if archive is not None:
        check_archive(store, listings, archive, now, max_checks=max_archive_checks, priority=archive_priority)
    evidence = store.archive_evidence_map() if hasattr(store, "archive_evidence_map") else {}
    leads = compute_hot_leads(listings, store.latest_run_starts(), now, threshold, lookback_days,
                              archive=evidence, reposts_only=reposts_only)
    before = store.hot_lead_status_map()
    new_keys = [l["lead_key"] for l in leads if l["lead_key"] not in before]

    # Newly hot only: one Reed details call each for the ad link and expiry date.
    details_used = 0
    if reed is not None:
        from .reed_client import CallBudget, ReedError
        budget = CallBudget(max_detail_calls)
        by_key = {l["lead_key"]: l for l in leads}
        for k in new_keys:
            lead = by_key[k]
            if lead["latest_source"] != "reed":
                continue
            try:
                d = reed.get_details(lead["latest_job_id"], budget=budget)
            except ReedError as exc:
                log.warning("details for job %s skipped: %s", lead["latest_job_id"], exc)
                break
            details_used += 1
            lead["latest_url"] = d.get("jobUrl") or d.get("externalUrl") or lead["latest_url"]
            from .reed_client import _parse_reed_date
            exp = _parse_reed_date(d.get("expirationDate"))
            if isinstance(exp, dt.date):
                lead["expiration_date"] = exp

    store.save_hot_leads(leads)
    removed = store.prune_hot_leads({l["lead_key"] for l in leads})
    log.info("hot leads: %d hot (%d new), %d cooled off and removed, %d details calls",
             len(leads), len(new_keys), removed, details_used)
    return {"hot": len(leads), "new": len(new_keys), "removed": removed, "details_calls": details_used}
