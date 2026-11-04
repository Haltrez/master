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
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from .normalise import lead_key

log = logging.getLogger("detect")

LONG_OPEN_DAYS = 30
LIVE_SLACK = dt.timedelta(hours=1)
# An ad only counts as "ended" once we've stopped seeing it for this long before the next one appears.
# (Within one run ads are seen seconds apart: that is the same ad in several places, not a repost.)
REPOST_GAP = dt.timedelta(days=2)
DEFAULT_LIVE_WINDOW = dt.timedelta(hours=36)


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
    groups: dict[str, list[Listing]] = defaultdict(list)
    for l in listings:
        if l.last_seen_at >= cutoff:
            groups[l.key].append(l)

    leads: list[dict[str, Any]] = []
    for key, ls in groups.items():
        by_source: dict[str, set[int]] = defaultdict(set)
        for l in ls:
            if l.first_seen_at >= cutoff:
                by_source[l.source].add(l.job_id)
        count = max((len(v) for v in by_source.values()), default=0)
        live = [l for l in ls if _is_live(l, latest_run, now)]
        live_by_source: dict[str, set[int]] = defaultdict(set)
        for l in live:
            live_by_source[l.source].add(l.job_id)
        day_one = any(len(v) >= 2 for v in live_by_source.values())
        long_open = any((l.last_seen_at - l.opened()).days >= LONG_OPEN_DAYS for l in ls)
        ev = archive.get(key) or {}
        prior = int(ev.get("prior_count") or 0)
        count += prior
        flagged = any(l.source_says_repost for l in ls)
        if flagged and count < 2:
            count = 2      # the board itself says this ad is a repost: at least two postings
        repost = _reposted(ls) or prior > 0 or flagged
        if reposts_only:
            if not (repost and count >= threshold):
                continue
        elif not (count >= threshold or long_open or day_one):
            continue
        latest = max(ls, key=lambda l: (l.first_seen_at, l.job_id))
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
        })

    hot_keys_per_employer: dict[str, int] = defaultdict(int)
    for lead in leads:
        hot_keys_per_employer[lead["employer_norm"]] += 1
    for lead in leads:
        lead["score"] = (2 * (lead["posting_count_90d"] - 1) + (2 if lead["long_open"] else 0)
                         + (1 if hot_keys_per_employer[lead["employer_norm"]] >= 3 else 0)
                         + (1 if lead["live_now"] else 0))
    leads.sort(key=lambda x: (-x["score"], x["employer_norm"], x["title_norm"]))
    return leads


def check_archive(store, listings: list[Listing], archive, now: dt.datetime, max_checks: int = 150) -> int:
    """Wayback lookups for live company+role groups not checked in 30 days (best-effort, capped)."""
    live: dict[str, Listing] = {}
    for l in listings:
        if l.last_seen_at >= now - dt.timedelta(days=2):
            cur = live.get(l.key)
            if cur is None or l.first_seen_at < cur.first_seen_at:
                live[l.key] = l
    fresh = store.archive_checked_since(now - dt.timedelta(days=30))
    todo = [k for k in live if k not in fresh][:max_checks]
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
                      max_archive_checks: int = 150, reposts_only: bool = False) -> dict[str, int]:
    """Recompute hot_leads from the database. Keeps every status the owner set."""
    now = now or dt.datetime.now(dt.timezone.utc)
    rows = store.employer_listings_since(now - dt.timedelta(days=lookback_days))
    listings = [Listing(**r) for r in rows]
    if archive is not None:
        check_archive(store, listings, archive, now, max_checks=max_archive_checks)
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
