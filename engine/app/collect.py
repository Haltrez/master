"""Run a saved search against Reed (and Adzuna when configured) and store every listing."""

from __future__ import annotations

import datetime as dt
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .config import Settings
from .db import Repo
from .normalise import employer_norm, location_norm, title_norm
from .reed_client import CallBudget, PostedBy, ReedClient, ReedError

log = logging.getLogger("collect")


@dataclass
class RunResult:
    run_id: int
    search_id: int
    status: str
    api_calls: int = 0
    listings_seen: int = 0
    new_listings: int = 0
    reclassified: int = 0
    adzuna_calls: int = 0
    theirstack_jobs: int = 0
    error: str | None = None
    by_posted_by: dict[str, int] = field(default_factory=lambda: {"employer": 0, "agency": 0})
    after_run: dict[str, Any] = field(default_factory=dict)


def agency_name_pattern(words: tuple[str, ...]) -> re.Pattern[str]:
    # Word-start match, so "recruit" catches "Recruiters" but not "Unrecruitable".
    alts = "|".join(re.escape(w.lower()) for w in sorted(words, key=len, reverse=True))
    return re.compile(r"\b(" + alts + r")", re.IGNORECASE)


# "Hays Accountancy & Finance", "Michael Page Engineering": a brand plus a
# recruiting-division word. "Hays Travel" stays an employer.
DIVISION_WORDS = {"accountancy", "finance", "recruitment", "construction", "property", "technology", "it",
                  "engineering", "specialist", "talent", "executive", "legal", "healthcare", "education",
                  "office", "support", "banking", "tax", "hr", "human", "resources", "digital", "energy",
                  "life", "sciences", "procurement", "supply", "chain", "sales", "marketing", "interim",
                  "contract", "temp", "temps", "industrial", "logistics", "uk", "group"}


def is_known_agency(norm: str, known: set[str] | None) -> bool:
    if not known or not norm:
        return False
    if norm in known:
        return True
    for brand in known:
        if norm.startswith(brand + " ") and len(brand) >= 4:
            rest = norm[len(brand) + 1:].split()
            if rest and rest[0] in DIVISION_WORDS:
                return True
    return False


# Agency wording in the ad text: "Our client is a leading...", "on behalf of our client".
# Singular "client" only: a law firm writing "our clients are..." is still an employer.
AGENCY_PHRASES = re.compile(
    r"\bon behalf of (?:our|my|a|an) client\b|\b(?:our|my) client(?:'s|,| is\b| has\b| are\b| based\b| seeks\b|"
    r" requires\b| a\b| an\b| who\b)|\bwe are recruiting for (?:a|an|our) client\b|\bclient of ours\b|"
    r"\bwe(?:'re| are) (?:exclusively )?(?:partnered|working) with (?:a|an|our) (?:client|leading)\b",
    re.IGNORECASE)


def classify(
    job: Any,
    queried_as: PostedBy,
    overrides: dict[str, bool],
    agency_pattern: re.Pattern[str],
    known_agencies: set[str] | None = None,
) -> tuple[PostedBy, str | None]:
    """Return (posted_by, reason-if-reclassified)."""
    norm = employer_norm(job.employer_name)
    if norm in overrides:
        forced: PostedBy = "agency" if overrides[norm] else "employer"
        return forced, (f"override is_agency={overrides[norm]}" if forced != queried_as else None)
    if queried_as == "employer":
        if is_known_agency(norm, known_agencies):
            return "agency", "known agency"
        m = agency_pattern.search(job.employer_name)
        if m:
            return "agency", f"name contains '{m.group(1).lower()}'"
        text = getattr(job, "job_description", None) or ""
        p = AGENCY_PHRASES.search(text)
        if p:
            return "agency", f"ad says '{p.group(0).strip()}'"
    return queried_as, None


def listing_row(job: Any, posted_by: PostedBy, source: str = "reed") -> dict[str, Any]:
    return {
        "job_id": job.job_id,
        "source": source,
        "posted_by": posted_by,
        "employer_id": job.employer_id,
        "employer_name": job.employer_name,
        "employer_norm": employer_norm(job.employer_name),
        "title": job.job_title,
        "title_norm": title_norm(job.job_title, job.location_name),
        "location_name": job.location_name,
        "location_norm": location_norm(job.location_name),
        "min_salary": job.minimum_salary,
        "max_salary": job.maximum_salary,
        "url": job.job_url,
        "posted_date": job.posted_date.isoformat() if job.posted_date else None,
        "expiration_date": job.expiration_date.isoformat() if job.expiration_date else None,
        "raw": job.raw,
    }


def _near(job, center, radius_miles: float, town: str) -> bool:
    """Keep a TheirStack job if it's within the radius (by its coordinates) or names the search town."""
    from .geo import miles_between
    if center and job.lat is not None and job.lng is not None:
        return miles_between(center, (float(job.lat), float(job.lng))) <= radius_miles
    return bool(town) and town.lower() in (job.location_name or "").lower()


def run_search(search_id: int, *, repo: Repo, reed: ReedClient | None, settings: Settings, adzuna=None,
               run_id: int | None = None, after_run: Callable[[RunResult], dict] | None = None,
               theirstack=None, geocoder=None) -> RunResult:
    search = repo.get_search(search_id)
    if search is None:
        raise ValueError(f"search {search_id} not found")

    run_id = run_id or repo.create_run(search_id)
    result = RunResult(run_id=run_id, search_id=search_id, status="running")
    budget = CallBudget(settings.reed_max_calls_per_run)
    adz_budget = CallBudget(settings.adzuna_max_calls_per_run)
    pattern = agency_name_pattern(settings.agency_name_words)
    seen: dict[int, PostedBy] = {}
    log.info("run %d: search %d '%s' started", run_id, search_id, search.label)

    def store_page(page, queried_as: PostedBy, source: str, known: set[str] | None) -> None:
        rows: dict[int, dict[str, Any]] = {}
        for job in page:
            posted_by, reason = classify(job, queried_as, overrides, pattern, known)
            if reason:
                result.reclassified += 1
                log.info("run %d: reclassified '%s' (job %d) %s -> %s: %s",
                         run_id, job.employer_name, job.job_id, queried_as, posted_by, reason)
            rows[job.job_id] = listing_row(job, posted_by, source)
            seen[job.job_id] = posted_by
        if rows:
            result.new_listings += repo.upsert_listings(search_id, list(rows.values()))

    try:
        if not search.keywords:
            raise ValueError("search has no keywords")
        if reed is None and adzuna is None and theirstack is None:
            raise ValueError("no job source configured (set REED_API_KEY and/or ADZUNA_APP_ID/ADZUNA_APP_KEY)")
        overrides = repo.get_overrides()
        reed_known = repo.known_agency_norms() if hasattr(repo, "known_agency_norms") else None
        if reed is not None:
            for keyword in search.keywords:
                for queried_as in ("employer", "agency"):
                    for page in reed.iter_search_pages(keyword, search.location, search.radius_miles,
                                                       search.min_salary, queried_as, budget=budget):  # type: ignore[arg-type]
                        store_page(page, queried_as, "reed", reed_known)  # type: ignore[arg-type]
        if adzuna is not None:
            known = set(settings.known_agency_brands)
            if hasattr(repo, "known_agency_norms"):
                known |= repo.known_agency_norms()
            try:
                for keyword in search.keywords:
                    for page in adzuna.iter_search_pages(keyword, search.location, search.radius_miles,
                                                         search.min_salary, budget=adz_budget):
                        store_page(page, "employer", "adzuna", known)
            except ReedError as exc:
                # Adzuna is a second source: a failure there doesn't fail the run.
                log.warning("run %d: adzuna stopped early: %s", run_id, exc)
        if theirstack is not None:
            known = set(settings.known_agency_brands)
            if hasattr(repo, "known_agency_norms"):
                known |= repo.known_agency_norms()
            # First run of a search pulls history (so reposts show from day one); later runs the last 3 days.
            flag = f"theirstack:backfilled:{search_id}"
            has_cache = hasattr(repo, "cache_get")
            backfilled = has_cache and repo.cache_get(flag) is not None
            days = 3 if backfilled else settings.theirstack_backfill_days
            if geocoder is None and has_cache:
                from .geo import Geocoder
                geocoder = Geocoder(repo)
            place = geocoder.geocode(search.location) if geocoder else None
            center = (place.lat, place.lng) if place else None
            try:
                for page in theirstack.iter_search_pages(list(search.keywords), max_age_days=days,
                                                         max_jobs=settings.theirstack_max_jobs_per_run):
                    near = [j for j in page if _near(j, center, search.radius_miles * 1.2, search.location)]
                    result.theirstack_jobs += len(page)
                    store_page(near, "employer", "theirstack", known)
                if has_cache:
                    repo.cache_set(flag, {"at": dt.datetime.now(dt.timezone.utc).isoformat()})
            except ReedError as exc:
                log.warning("run %d: theirstack stopped early: %s", run_id, exc)
        result.status = "ok"
    except Exception as exc:
        result.status = "error"
        result.error = str(exc) or type(exc).__name__
        log.exception("run %d: failed: %s", run_id, result.error)
    finally:
        result.api_calls = budget.used
        result.adzuna_calls = adz_budget.used
        result.listings_seen = len(seen)
        for pb in seen.values():
            result.by_posted_by[pb] += 1
        repo.finish_run(run_id, status=result.status, api_calls=result.api_calls + result.adzuna_calls,
                        listings_seen=result.listings_seen, new_listings=result.new_listings,
                        error=result.error)
        repo.touch_search(search_id)
        log.info("run %d: %s reed_calls=%d adzuna_calls=%d listings_seen=%d new_listings=%d employer=%d agency=%d "
                 "reclassified=%d", run_id, result.status, result.api_calls, result.adzuna_calls,
                 result.listings_seen, result.new_listings, result.by_posted_by["employer"],
                 result.by_posted_by["agency"], result.reclassified)
    if after_run is not None:
        try:
            result.after_run = after_run(result) or {}
        except Exception as exc:  # rebuilding must not hide the run result
            log.exception("run %d: after-run rebuild failed: %s", run_id, exc)
            result.after_run = {"error": str(exc)}
    return result


def run_all(*, repo: Repo, reed: ReedClient | None, settings: Settings, adzuna=None, theirstack=None,
            after_run: Callable[[RunResult], dict] | None = None,
            run_ids: dict[int, int] | None = None) -> list[RunResult]:
    """Run every active search one after another. One failure doesn't stop the rest.
    Hot leads and agencies are rebuilt once at the end (after_run gets the last result)."""
    results = []
    searches = repo.list_active_searches()
    for search in searches:
        results.append(run_search(search.id, repo=repo, reed=reed, settings=settings, adzuna=adzuna,
                                  theirstack=theirstack,
                                  run_id=(run_ids or {}).get(search.id)))
    if after_run is not None and results:
        try:
            results[-1].after_run = after_run(results[-1]) or {}
        except Exception as exc:
            log.exception("after-run rebuild failed: %s", exc)
    return results
