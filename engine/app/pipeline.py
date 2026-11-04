"""Wiring shared by the CLI, the API and scheduled jobs: build clients from
settings, run collection, then rebuild hot leads and agencies."""

from __future__ import annotations

import logging
from typing import Any

from .collect import RunResult, run_all, run_search
from .config import Settings

log = logging.getLogger("pipeline")


def make_store(settings: Settings):
    from .pg import PgStore
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL is not set (see .env.example)")
    return PgStore(settings.database_url)


def make_reed(settings: Settings):
    if not settings.reed_api_key:
        return None
    from .reed_client import ReedClient
    return ReedClient(settings.reed_api_key, requests_per_second=settings.reed_requests_per_second,
                      max_tries=settings.reed_max_tries, backoff_base=settings.reed_backoff_base_seconds,
                      base_url=settings.reed_base_url)


def make_adzuna(settings: Settings):
    if not (settings.adzuna_app_id and settings.adzuna_app_key):
        return None
    from .adzuna_client import AdzunaClient
    return AdzunaClient(settings.adzuna_app_id, settings.adzuna_app_key)


def make_theirstack(settings: Settings):
    if not settings.theirstack_api_key:
        return None
    from .theirstack_client import TheirStackClient
    return TheirStackClient(settings.theirstack_api_key)


def make_extra_sources(settings: Settings) -> list:
    from .sources import ApifyIndeedClient, CareerjetClient, JoobleClient
    out = []
    if settings.careerjet_api_key:
        out.append(CareerjetClient(settings.careerjet_api_key, settings.careerjet_user_ip))
    if settings.jooble_api_key:
        out.append(JoobleClient(settings.jooble_api_key, max_requests=settings.jooble_max_requests_per_run))
    if settings.apify_token:
        out.append(ApifyIndeedClient(settings.apify_token, actor=settings.apify_indeed_actor,
                                     max_items=settings.apify_indeed_max_items))
    return out


def threshold(store, settings: Settings) -> int:
    try:
        return int(store.setting("repost_threshold", settings.repost_threshold))
    except (TypeError, ValueError):
        return settings.repost_threshold


def rebuild_all(store, settings: Settings, reed=None) -> dict[str, Any]:
    """Hot leads, then prospect agencies. Called after every collection run."""
    from .agencies import rebuild_agencies
    from .detect import rebuild_hot_leads
    out: dict[str, Any] = {}
    from .wayback import ArchiveReposts, Wayback

    def flag(key, default):
        v = store.setting(key, default)
        return v if v is not None else default

    out["hot_leads"] = rebuild_hot_leads(
        store, archive_priority=order_priority(store), threshold=threshold(store, settings), lookback_days=settings.lookback_days, reed=reed,
        archive=ArchiveReposts(Wayback(store, max_consecutive_failures=5)) if flag("archive_checks_per_run", 150) else None,
        max_archive_checks=int(flag("archive_checks_per_run", 150)), reposts_only=bool(flag("reposts_only", True)))
    out["agencies"] = rebuild_agencies(store, lookback_days=settings.lookback_days)
    from .lead_contacts import enrich_new_lead_companies
    try:
        out["lead_contacts"] = enrich_new_lead_companies(store, settings)
    except Exception as exc:  # contacts are a bonus; never fail the run over them
        out["lead_contacts"] = {"error": str(exc)}
    return out


def order_priority(store):
    """Score a listing for archive checks: 2 = in an open order's territory and its title matches the
    order's industry, 1 = in the territory, 0 = anything else."""
    from .collect import title_matches
    rows = store.q("""select o.search_id, s.keywords from lead_orders o join searches s on s.id = o.search_id
                      where o.status in ('collecting', 'waiting', 'error')""") if hasattr(store, "q") else []
    kws = {r["search_id"]: list(r["keywords"] or []) for r in rows}
    if not kws:
        return None

    def score(l) -> int:
        best = 0
        for sid in l.search_ids:
            if sid in kws:
                best = max(best, 2 if any(title_matches(k, l.title) for k in kws[sid]) else 1)
        return best
    return score


def collect_one(store, settings: Settings, search_id: int, run_id: int | None = None) -> RunResult:
    reed, adzuna = make_reed(settings), make_adzuna(settings)
    return run_search(search_id, repo=store, reed=reed, settings=settings, adzuna=adzuna, run_id=run_id,
                      theirstack=make_theirstack(settings), extra_sources=make_extra_sources(settings),
                      after_run=lambda r: rebuild_all(store, settings, reed))


def collect_all(store, settings: Settings, run_ids: dict[int, int] | None = None) -> list[RunResult]:
    reed, adzuna = make_reed(settings), make_adzuna(settings)
    return run_all(repo=store, reed=reed, settings=settings, adzuna=adzuna, run_ids=run_ids,
                   theirstack=make_theirstack(settings), extra_sources=make_extra_sources(settings),
                   after_run=lambda r: rebuild_all(store, settings, reed))


def top_up_orders(store, settings: Settings) -> list[dict]:
    """Send what every open lead order still owes (after the daily collection)."""
    from .google_sheets import Sheets
    from .orders import top_up_all
    strict = store.setting("verified_companies_only", True)
    return top_up_all(store, settings, sheets=Sheets.from_store(store, settings), strict=True if strict is None else bool(strict),
                      tools=make_tools(store, settings))


def make_tools(store, settings: Settings):
    """Everything an order needs to check and enrich leads (each part optional)."""
    from .leadgen import Tools
    from .lead_contacts import hunter_for
    from .qualify import make_client
    ch = None
    if settings.companies_house_api_key:
        from .ch_client import CachedCompaniesHouse, CompaniesHouseClient
        ch = CachedCompaniesHouse(CompaniesHouseClient(settings.companies_house_api_key), store)
    brave = None
    if settings.brave_api_key:
        from .web_lookup import Brave
        brave = Brave(settings.brave_api_key)
    from .geo import Geocoder
    return Tools(claude=make_client(settings), ch=ch, reed=make_reed(settings), brave=brave,
                 hunter=hunter_for(store, settings), geocoder=Geocoder(store))
