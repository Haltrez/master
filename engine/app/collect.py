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
    by_source: dict[str, int] = field(default_factory=dict)
    source_errors: dict[str, str] = field(default_factory=dict)
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


def title_matches(keyword: str, title: str) -> bool:
    """Every word of the keyword starts a word of the title: 'management accountant' keeps
    'Financial/Management Accountant' and drops 'Account Manager' (aggregators match loosely)."""
    kw = [w for w in re.sub(r"[^a-z0-9&]+", " ", keyword.lower()).split() if len(w) > 1]
    words = re.sub(r"[^a-z0-9&]+", " ", (title or "").lower()).split()
    return all(any(t.startswith(k) for t in words) for k in kw)


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
        "source_says_repost": bool(getattr(job, "source_says_repost", False) or getattr(job, "reposted", False)),
    }


def _near(job, center, radius_miles: float, town: str) -> bool:
    """Keep a TheirStack job if it's within the radius (by its coordinates) or names the search town."""
    from .geo import miles_between
    if center and job.lat is not None and job.lng is not None:
        return miles_between(center, (float(job.lat), float(job.lng))) <= radius_miles
    return bool(town) and town.lower() in (job.location_name or "").lower()


def run_search(search_id: int, *, repo: Repo, reed: ReedClient | None, settings: Settings, adzuna=None,
               run_id: int | None = None, after_run: Callable[[RunResult], dict] | None = None,
               theirstack=None, geocoder=None, extra_sources=None) -> RunResult:
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
        result.by_source[source] = result.by_source.get(source, 0) + len(page)
        if rows:
            result.new_listings += repo.upsert_listings(search_id, list(rows.values()))

    try:
        if not search.keywords:
            raise ValueError("search has no keywords")
        if reed is None and adzuna is None and theirstack is None and not extra_sources:
            raise ValueError("no job source configured (set REED_API_KEY and/or ADZUNA_APP_ID/ADZUNA_APP_KEY)")
        overrides = repo.get_overrides()
        has_cache = hasattr(repo, "cache_get")

        def known_agencies() -> set[str]:
            k = set(settings.known_agency_brands)
            if hasattr(repo, "known_agency_norms"):
                k |= repo.known_agency_norms()
            return k

        attempted: list[str] = []

        def run_source(name: str, work: Callable[[], None]) -> None:
            """Each source on its own: one failing (bad key, quota) never stops the others.
            The outcome is saved for the Settings page."""
            attempted.append(name)
            before = result.by_source.get(name, 0)
            status: dict[str, Any] = {"at": dt.datetime.now(dt.timezone.utc).isoformat(), "search_id": search_id}
            try:
                work()
                status.update(ok=True, ads=result.by_source.get(name, 0) - before)
            except Exception as exc:
                log.warning("run %d: %s failed: %s", run_id, name, exc)
                status.update(ok=False, error=str(exc)[:300] or type(exc).__name__,
                              ads=result.by_source.get(name, 0) - before)
                result.source_errors[name] = status["error"]
            if hasattr(repo, "cache_set"):
                repo.cache_set(f"source_status:{name}", status)

        def backfill_flag(name: str) -> tuple[str, bool]:
            flag = f"{name}:backfilled:{search_id}"
            return flag, bool(has_cache and repo.cache_get(flag) is not None)

        def mark_backfilled(flag: str) -> None:
            if hasattr(repo, "cache_set"):
                repo.cache_set(flag, {"at": dt.datetime.now(dt.timezone.utc).isoformat()})

        if reed is not None:
            reed_known = repo.known_agency_norms() if hasattr(repo, "known_agency_norms") else None

            def do_reed():
                for keyword in search.keywords:
                    for queried_as in ("employer", "agency"):
                        for page in reed.iter_search_pages(keyword, search.location, search.radius_miles,
                                                           search.min_salary, queried_as, budget=budget):  # type: ignore[arg-type]
                            store_page(page, queried_as, "reed", reed_known)  # type: ignore[arg-type]
            run_source("reed", do_reed)

        if adzuna is not None:
            def do_adzuna():
                known = known_agencies()
                flag, seen_before = backfill_flag("adzuna")
                for keyword in search.keywords:
                    for page in adzuna.iter_search_pages(keyword, search.location, search.radius_miles,
                                                         search.min_salary, budget=adz_budget,
                                                         max_days_old=3 if seen_before else 30):
                        store_page([j for j in page if title_matches(keyword, j.job_title)], "employer", "adzuna", known)
                mark_backfilled(flag)
            run_source("adzuna", do_adzuna)

        if theirstack is not None:
            def do_theirstack():
                nonlocal geocoder
                known = known_agencies()
                # First run of a search pulls history (so reposts show from day one); later runs the last 3 days.
                flag, backfilled = backfill_flag("theirstack")
                days = 3 if backfilled else settings.theirstack_backfill_days
                if geocoder is None and has_cache:
                    from .geo import Geocoder
                    geocoder = Geocoder(repo)
                place = geocoder.geocode(search.location) if geocoder else None
                center = (place.lat, place.lng) if place else None
                for page in theirstack.iter_search_pages(list(search.keywords), max_age_days=days,
                                                         max_jobs=settings.theirstack_max_jobs_per_run):
                    near = [j for j in page if _near(j, center, search.radius_miles * 1.2, search.location)]
                    result.theirstack_jobs += len(page)
                    store_page(near, "employer", "theirstack", known)
                mark_backfilled(flag)
            run_source("theirstack", do_theirstack)

        for src in extra_sources or []:
            def do_extra(src=src):
                known = known_agencies()
                flag, seen_before = backfill_flag(src.name)
                for keyword in search.keywords:
                    for page in src.pages(keyword, search.location, search.radius_miles, not seen_before):
                        store_page([j for j in page if title_matches(keyword, j.job_title)], "employer", src.name, known)
                mark_backfilled(flag)
            run_source(src.name, do_extra)

        # Reed is the main source: its failure (bad key, call cap) marks the run as an error.
        # Other sources failing (e.g. a used-up quota) only shows on the Settings page.
        if "reed" in result.source_errors:
            raise ReedError(result.source_errors["reed"])
        if attempted and len(result.source_errors) == len(attempted):
            raise ReedError("every source failed: " + "; ".join(f"{k}: {v}" for k, v in result.source_errors.items()))
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


def run_all(*, repo: Repo, reed: ReedClient | None, settings: Settings, adzuna=None, theirstack=None, extra_sources=None,
            after_run: Callable[[RunResult], dict] | None = None,
            run_ids: dict[int, int] | None = None) -> list[RunResult]:
    """Run every active search one after another. One failure doesn't stop the rest.
    Hot leads and agencies are rebuilt once at the end (after_run gets the last result)."""
    results = []
    searches = repo.list_active_searches()
    for search in searches:
        results.append(run_search(search.id, repo=repo, reed=reed, settings=settings, adzuna=adzuna,
                                  theirstack=theirstack, extra_sources=extra_sources,
                                  run_id=(run_ids or {}).get(search.id)))
    if after_run is not None and results:
        try:
            results[-1].after_run = after_run(results[-1]) or {}
        except Exception as exc:
            log.exception("after-run rebuild failed: %s", exc)
    return results
