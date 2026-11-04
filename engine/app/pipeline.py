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
    out["hot_leads"] = rebuild_hot_leads(store, threshold=threshold(store, settings),
                                         lookback_days=settings.lookback_days, reed=reed)
    out["agencies"] = rebuild_agencies(store, lookback_days=settings.lookback_days)
    from .lead_contacts import enrich_new_lead_companies
    try:
        out["lead_contacts"] = enrich_new_lead_companies(store, settings)
    except Exception as exc:  # contacts are a bonus; never fail the run over them
        out["lead_contacts"] = {"error": str(exc)}
    return out


def collect_one(store, settings: Settings, search_id: int, run_id: int | None = None) -> RunResult:
    reed, adzuna = make_reed(settings), make_adzuna(settings)
    return run_search(search_id, repo=store, reed=reed, settings=settings, adzuna=adzuna, run_id=run_id,
                      after_run=lambda r: rebuild_all(store, settings, reed))


def collect_all(store, settings: Settings, run_ids: dict[int, int] | None = None) -> list[RunResult]:
    reed, adzuna = make_reed(settings), make_adzuna(settings)
    return run_all(repo=store, reed=reed, settings=settings, adzuna=adzuna, run_ids=run_ids,
                   after_run=lambda r: rebuild_all(store, settings, reed))
