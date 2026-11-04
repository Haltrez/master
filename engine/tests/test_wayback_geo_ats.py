import datetime as dt

import httpx

from app.ats import detect_ats, parse_ats
from app.geo import Geocoder, miles_between
from app.role_repo import LocalRoleRepo
from app.wayback import Wayback
from tests.role_fakes import Clock, FakeWeb


def repo(clock):
    return LocalRoleRepo(None, clock=clock, seed=False)


def test_wayback_earliest_capture_and_cache():
    clock = Clock()
    web = FakeWeb(wayback={"https://x.co.uk/jobs/1": "20250912083000"})
    r = repo(clock)
    wb = Wayback(r, transport=web.transport, sleep=clock.sleep, clock=clock.monotonic)
    assert wb.first_capture("https://x.co.uk/jobs/1") == dt.date(2025, 9, 12)
    assert wb.first_capture("https://x.co.uk/jobs/2") is None             # no capture -> unknown
    q = web.requests[0].url.params
    assert (q["output"], q["fl"], q["filter"], q["limit"]) == ("json", "timestamp,statuscode", "statuscode:200", "1")
    n = len(web.requests)
    wb.first_capture("https://x.co.uk/jobs/1")
    assert len(web.requests) == n                                          # cached
    clock.advance(days=31)
    wb.first_capture("https://x.co.uk/jobs/1")
    assert len(web.requests) == n + 1                                      # cache is 30 days
    assert 1.0 in clock.sleeps                                             # 1 request per second


def test_wayback_down_shows_unknown_and_stops_trying():
    clock = Clock()
    web = FakeWeb(wayback_down=True)
    wb = Wayback(repo(clock), transport=web.transport, sleep=clock.sleep, clock=clock.monotonic)
    assert [wb.first_capture(f"https://x.co.uk/{i}") for i in range(5)] == [None] * 5
    assert len(web.requests) == 2 and wb.disabled


def test_geocode_prefers_bigger_place_and_caches_forever():
    clock = Clock()
    web = FakeWeb()
    r = repo(clock)
    g = Geocoder(r, transport=web.transport, sleep=clock.sleep, clock=clock.monotonic)
    p = g.geocode("Preston")
    assert (p.name, p.region) == ("Preston", "North West")                # City beats the Hamlet
    assert g.geocode("Westhoughton, Bolton").name == "Westhoughton"
    assert g.geocode("Manchester City Centre").name == "Manchester"       # trims to a known place
    assert g.geocode("M1 2JQ").name == "M1 2JQ"                           # postcode lookup
    assert g.geocode("Atlantis") is None
    n = len(web.requests)
    for q in ("Preston", "Atlantis", "M1 2JQ"):
        g.geocode(q)
    assert len(web.requests) == n


def test_miles_between():
    warrington, manchester = (53.3896, -2.5909), (53.4789, -2.2453)
    assert 15 < miles_between(warrington, manchester) < 17


def test_ats_detection_and_parsing():
    gh = detect_ats("https://boards.greenhouse.io/acme")
    assert gh.api_url == "https://boards-api.greenhouse.io/v1/boards/acme/jobs"
    assert detect_ats("https://jobs.lever.co/acme/123").api_url == "https://api.lever.co/v0/postings/acme?mode=json"
    assert detect_ats("https://jobs.eu.lever.co/acme").api_url.startswith("https://api.eu.lever.co/")
    assert detect_ats("https://jobs.ashbyhq.com/acme").api_url == "https://api.ashbyhq.com/posting-api/job-board/acme"
    assert detect_ats("https://hr.breathehr.com/v/x") is None
    assert detect_ats("https://acme.co.uk/careers") is None

    g = parse_ats("greenhouse", {"jobs": [{"title": "Electrical Estimator", "location": {"name": "Warrington"},
                                            "absolute_url": "https://boards.greenhouse.io/acme/jobs/1"}]})
    assert (g[0].title, g[0].location_text) == ("Electrical Estimator", "Warrington")
    lv = parse_ats("lever", [{"text": "M&E Project Manager", "categories": {"location": "Manchester"},
                              "hostedUrl": "https://jobs.lever.co/acme/1", "createdAt": 1756684800000}])
    assert (lv[0].title, lv[0].location_text, lv[0].date_posted) == ("M&E Project Manager", "Manchester", "2025-09-01")
    ab = parse_ats("ashby", {"jobs": [{"title": "HVAC Design Engineer", "location": "Preston", "isListed": True,
                                       "jobUrl": "https://jobs.ashbyhq.com/acme/1", "publishedAt": "2026-10-01T00:00:00Z"},
                                      {"title": "Hidden", "isListed": False}]})
    assert [j.title for j in ab] == ["HVAC Design Engineer"]
