"""Fake web for Role Match tests: the three seed careers sites, postcodes.io and Wayback."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import httpx

FIX = Path(__file__).parent / "fixtures"

PLACES = {
    "warrington": ("Warrington", "Town", 53.3896, -2.5909, "North West"),
    "manchester": ("Manchester", "City", 53.4789, -2.2453, "North West"),
    "westhoughton": ("Westhoughton", "Town", 53.5489, -2.5227, "North West"),
    "bolton": ("Bolton", "Town", 53.5777, -2.4310, "North West"),
    "preston": ("Preston", "City", 53.7606, -2.6985, "North West"),
    "cardiff": ("Cardiff", "City", 51.4800, -3.1769, "Wales"),
}

ROBOTS = {
    "www.dl-bsl.co.uk": "Sitemap: https://www.dl-bsl.co.uk/sitemap.xml\n",
    "celectrical.com": "User-agent: *\nDisallow: /wp-admin/\nAllow: /wp-admin/admin-ajax.php\n",
    "b-enggroup.co.uk": "User-agent: *\nDisallow: /wp-admin/\nAllow: /wp-admin/admin-ajax.php\n",
    "hr.breathehr.com": "User-Agent: *\nDisallow: /\nAllow: /recruitment/vacancies/\nAllow: /v/\n",
}

PAGES = {
    "https://www.dl-bsl.co.uk/job-board": "careers_dl.html",
    "https://celectrical.com/careers/": "careers_ce.html",
    "https://b-enggroup.co.uk/jobs/": "careers_be.html",
}

DETAIL_HTML = """<html><body><h1>{title}</h1><p>Building services contractor, M&amp;E projects.</p>
<script type="application/ld+json">{{"@context":"https://schema.org","@type":"JobPosting","title":"{title}",
"datePosted":"2026-08-20","jobLocation":{{"@type":"Place","address":{{"addressLocality":"Manchester"}}}}}}</script>
</body></html>"""


class FakeWeb:
    def __init__(self, robots: dict[str, str] | None = None, pages: dict[str, str] | None = None,
                 wayback: dict[str, str | None] | None = None, wayback_down: bool = False):
        self.robots = dict(ROBOTS if robots is None else robots)
        self.pages = {u: (FIX / f).read_text(encoding="utf-8") for u, f in PAGES.items()}
        if pages:
            self.pages.update(pages)
        self.wayback = wayback or {}
        self.wayback_down = wayback_down
        self.requests: list[httpx.Request] = []

    def handler(self, req: httpx.Request) -> httpx.Response:
        self.requests.append(req)
        host, path, url = req.url.host, req.url.path, str(req.url).split("?")[0]
        if host == "api.postcodes.io":
            if path.startswith("/places"):
                q = req.url.params["q"].lower()
                p = PLACES.get(q)
                result = [] if not p else [
                    {"name_1": p[0], "local_type": "Hamlet", "latitude": 50.0, "longitude": 0.0, "region": "Elsewhere"},
                    {"name_1": p[0], "local_type": p[1], "latitude": p[2], "longitude": p[3], "region": p[4]}]
                return httpx.Response(200, json={"status": 200, "result": result})
            if path.startswith("/postcodes/"):
                return httpx.Response(200, json={"status": 200, "result": {
                    "postcode": "M1 2JQ", "latitude": 53.481, "longitude": -2.226, "region": "North West"}})
        if host == "web.archive.org":
            if self.wayback_down:
                raise httpx.ConnectError("down", request=req)
            target = req.url.params["url"]
            ts = self.wayback.get(target)
            rows = [["timestamp", "statuscode"], [ts, "200"]] if ts else []
            return httpx.Response(200, json=rows)
        if path == "/robots.txt":
            if host in self.robots:
                return httpx.Response(200, text=self.robots[host])
            return httpx.Response(404, text="not found")
        if url in self.pages:
            return httpx.Response(200, text=self.pages[url], headers={"content-type": "text/html"})
        if host in ("www.dl-bsl.co.uk", "hr.breathehr.com", "b-enggroup.co.uk") and path not in ("/",):
            title = path.rstrip("/").rsplit("/", 1)[-1].replace("-", " ").title()
            return httpx.Response(200, text=DETAIL_HTML.format(title=title), headers={"content-type": "text/html"})
        return httpx.Response(404, text="nope")

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)

    def hits(self, host: str) -> list[str]:
        return [str(r.url) for r in self.requests if r.url.host == host]


class Clock:
    """Wall clock (datetimes) + monotonic clock (floats) driven together."""

    def __init__(self, start: dt.datetime | None = None):
        self.now = start or dt.datetime(2026, 11, 4, 9, 0, tzinfo=dt.timezone.utc)
        self.mono = 1000.0
        self.sleeps: list[float] = []

    def __call__(self) -> dt.datetime:
        return self.now

    def monotonic(self) -> float:
        return self.mono

    def sleep(self, s: float) -> None:
        self.sleeps.append(s)
        self.mono += s
        self.now += dt.timedelta(seconds=s)

    def advance(self, **kw) -> None:
        d = dt.timedelta(**kw)
        self.now += d
        self.mono += d.total_seconds()
