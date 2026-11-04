"""Optional built-in scheduler (docker compose --profile scheduler).

Make.com is the main scheduler (make/SCENARIOS.md). This is a fallback that
runs the daily collection at 06:00 UK time without Make.com.
"""

from __future__ import annotations

import datetime as dt
import logging
import time
from zoneinfo import ZoneInfo

from .config import get_settings, setup_logging
from .pipeline import collect_all, make_store

UK = ZoneInfo("Europe/London")
log = logging.getLogger("scheduler")


def next_run(now: dt.datetime, hour: int = 6) -> dt.datetime:
    target = now.astimezone(UK).replace(hour=hour, minute=0, second=0, microsecond=0)
    if target <= now:
        target += dt.timedelta(days=1)
    return target


def main() -> None:
    settings = get_settings()
    setup_logging(settings)
    while True:
        now = dt.datetime.now(UK)
        nxt = next_run(now)
        log.info("next collection at %s", nxt.isoformat())
        time.sleep(max(1, (nxt - now).total_seconds()))
        try:
            results = collect_all(make_store(settings), settings)
            log.info("collection done: %s", [(r.search_id, r.status) for r in results])
        except Exception:
            log.exception("scheduled collection failed")


if __name__ == "__main__":
    main()
