"""Built-in scheduler (docker compose --profile scheduler).

06:00 UK   collect every territory, then top up lead orders
07:30 UK   outreach auto-push (only when Settings → Outreach → auto-push is on)
every 15m  Smartlead: sync replies, refresh stats, health check (auto-pauses a campaign over 3% bounces)
"""

from __future__ import annotations

import datetime as dt
import logging
import time
from zoneinfo import ZoneInfo

from .config import get_settings, setup_logging
from .pipeline import collect_all, make_store, top_up_orders

UK = ZoneInfo("Europe/London")
log = logging.getLogger("scheduler")


def next_run(now: dt.datetime, hour: int = 6, minute: int = 0) -> dt.datetime:
    target = now.astimezone(UK).replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += dt.timedelta(days=1)
    return target


def due(now: dt.datetime, last: dt.date | None, hour: int, minute: int) -> bool:
    """A daily job is due once its UK time has passed today and it hasn't run today."""
    local = now.astimezone(UK)
    return (local.hour, local.minute) >= (hour, minute) and last != local.date()


def collection(settings) -> None:
    store = make_store(settings)
    results = collect_all(store, settings)
    log.info("collection done: %s", [(r.search_id, r.status) for r in results])
    log.info("orders topped up: %s", [(o["order_id"], o["sent"], o["status"]) for o in top_up_orders(store, settings)])


def outreach_every_15(settings) -> None:
    from . import outreach as OR
    from .smartlead import Smartlead
    store = make_store(settings)
    sl = Smartlead(settings.smartlead_api_key, base_url=settings.smartlead_base_url)
    for name, fn in (("replies", lambda: OR.sync_replies(store, sl)), ("stats", lambda: OR.stats(store, sl, refresh=True)),
                     ("health", lambda: OR.health(store, sl))):
        try:
            fn()
        except Exception as exc:   # one failing job never stops the others
            log.warning("outreach %s failed: %s", name, exc)


def outreach_auto_push(settings) -> None:
    from . import outreach as OR
    from .smartlead import Smartlead
    store = make_store(settings)
    log.info("outreach auto-push: %s", OR.auto_push(store, settings, Smartlead(settings.smartlead_api_key,
                                                                              base_url=settings.smartlead_base_url)))


def main() -> None:
    settings = get_settings()
    setup_logging(settings)
    last_collect: dt.date | None = None
    last_push: dt.date | None = None
    last_15: float = 0.0
    now = dt.datetime.now(UK)
    if (now.hour, now.minute) >= (6, 0):
        last_collect = now.date()       # started after 06:00: first collection tomorrow, as before
    if (now.hour, now.minute) >= (7, 30):
        last_push = now.date()
    log.info("scheduler up: collection 06:00, outreach auto-push 07:30 (if on), Smartlead sync every 15 min")
    while True:
        now = dt.datetime.now(UK)
        try:
            if due(now, last_collect, 6, 0):
                last_collect = now.date()
                collection(settings)
            if settings.smartlead_api_key and due(now, last_push, 7, 30):
                last_push = now.date()
                outreach_auto_push(settings)
            if settings.smartlead_api_key and time.monotonic() - last_15 >= 15 * 60:
                last_15 = time.monotonic()
                outreach_every_15(settings)
        except Exception:
            log.exception("scheduled job failed")
        time.sleep(30)


if __name__ == "__main__":
    main()
