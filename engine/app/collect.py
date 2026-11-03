"""Run a saved search against Reed and store every listing."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from .config import Settings
from .db import Repo
from .normalise import employer_norm, location_norm, title_norm
from .reed_client import CallBudget, PostedBy, ReedClient, ReedJob

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
    error: str | None = None
    by_posted_by: dict[str, int] = field(default_factory=lambda: {"employer": 0, "agency": 0})


def agency_name_pattern(words: tuple[str, ...]) -> re.Pattern[str]:
    # Word-start match, so "recruit" catches "Recruiters" but not "Unrecruitable".
    alts = "|".join(re.escape(w.lower()) for w in sorted(words, key=len, reverse=True))
    return re.compile(r"\b(" + alts + r")", re.IGNORECASE)


def classify(
    job: ReedJob,
    queried_as: PostedBy,
    overrides: dict[str, bool],
    agency_pattern: re.Pattern[str],
) -> tuple[PostedBy, str | None]:
    """Return (posted_by, reason-if-reclassified)."""
    norm = employer_norm(job.employer_name)
    if norm in overrides:
        forced: PostedBy = "agency" if overrides[norm] else "employer"
        return forced, (f"override is_agency={overrides[norm]}" if forced != queried_as else None)
    if queried_as == "employer":
        m = agency_pattern.search(job.employer_name)
        if m:
            return "agency", f"name contains '{m.group(1).lower()}'"
    return queried_as, None


def listing_row(job: ReedJob, posted_by: PostedBy) -> dict[str, Any]:
    return {
        "job_id": job.job_id,
        "source": "reed",
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


def run_search(search_id: int, *, repo: Repo, reed: ReedClient, settings: Settings) -> RunResult:
    search = repo.get_search(search_id)
    if search is None:
        raise ValueError(f"search {search_id} not found")

    run_id = repo.create_run(search_id)
    result = RunResult(run_id=run_id, search_id=search_id, status="running")
    budget = CallBudget(settings.reed_max_calls_per_run)
    pattern = agency_name_pattern(settings.agency_name_words)
    seen: dict[int, PostedBy] = {}
    log.info("run %d: search %d '%s' started", run_id, search_id, search.label)

    try:
        if not search.keywords:
            raise ValueError("search has no keywords")
        overrides = repo.get_overrides()
        for keyword in search.keywords:
            for queried_as in ("employer", "agency"):
                pages = reed.iter_search_pages(
                    keyword, search.location, search.radius_miles, search.min_salary,
                    queried_as, budget=budget,  # type: ignore[arg-type]
                )
                for page in pages:
                    rows: dict[int, dict[str, Any]] = {}
                    for job in page:
                        posted_by, reason = classify(job, queried_as, overrides, pattern)  # type: ignore[arg-type]
                        if reason:
                            result.reclassified += 1
                            log.info("run %d: reclassified '%s' (job %d) %s -> %s: %s",
                                     run_id, job.employer_name, job.job_id, queried_as, posted_by, reason)
                        rows[job.job_id] = listing_row(job, posted_by)
                        seen[job.job_id] = posted_by
                    if rows:
                        result.new_listings += repo.upsert_listings(search_id, list(rows.values()))
        result.status = "ok"
    except Exception as exc:
        result.status = "error"
        result.error = str(exc) or type(exc).__name__
        log.exception("run %d: failed: %s", run_id, result.error)
    finally:
        result.api_calls = budget.used
        result.listings_seen = len(seen)
        for pb in seen.values():
            result.by_posted_by[pb] += 1
        repo.finish_run(run_id, status=result.status, api_calls=result.api_calls,
                        listings_seen=result.listings_seen, new_listings=result.new_listings,
                        error=result.error)
        repo.touch_search(search_id)
        log.info("run %d: %s api_calls=%d listings_seen=%d new_listings=%d employer=%d agency=%d reclassified=%d",
                 run_id, result.status, result.api_calls, result.listings_seen, result.new_listings,
                 result.by_posted_by["employer"], result.by_posted_by["agency"], result.reclassified)
    return result


def run_all(*, repo: Repo, reed: ReedClient, settings: Settings) -> list[RunResult]:
    """Run every active search one after another. One failure doesn't stop the rest."""
    results = []
    for search in repo.list_active_searches():
        results.append(run_search(search.id, repo=repo, reed=reed, settings=settings))
    return results
