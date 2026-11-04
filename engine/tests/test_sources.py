import base64
import datetime as dt
import json

import httpx

from app.collect import run_search
from app.config import Settings
from app.detect import rebuild_hot_leads
from app.sources import OFFSETS, ApifyIndeedClient, CareerjetClient, JoobleClient


def careerjet(items, pages=1, calls=None):
    def h(req):
        if calls is not None:
            calls.append(req)
        return httpx.Response(200, json={"type": "JOBS", "hits": len(items), "pages": pages, "jobs": items})
    return CareerjetClient("cj-key", "185.0.0.1", transport=httpx.MockTransport(h), min_interval=0, sleep=lambda s: None)


CJ_ITEM = {"title": "Management Accountant", "company": "Harlow Group Ltd", "date": "Wed,15 Oct 2026 19:13:43 GMT",
           "description": "Join our finance team", "locations": "Leeds", "salary": "GBP 40000 - 45000 per year",
           "salary_min": 40000, "salary_max": 45000, "salary_type": "Y", "url": "https://jobviewtrack.com/v2/abc"}


def test_careerjet_request_and_parsing():
    calls = []
    pages = list(careerjet([CJ_ITEM], calls=calls).pages("management accountant", "Leeds", 15, True))
    q = calls[0].url.params
    assert (q["locale_code"], q["user_ip"], q["page_size"], q["sort"]) == ("en_GB", "185.0.0.1", "100", "date")
    assert calls[0].headers["Authorization"] == "Basic " + base64.b64encode(b"cj-key:").decode()
    j = pages[0][0]
    assert j.employer_name == "Harlow Group Ltd" and j.posted_date == dt.date(2026, 10, 15) and j.maximum_salary == 45000
    assert OFFSETS["careerjet"] <= j.job_id < OFFSETS["careerjet"] + 10**15
    again = list(careerjet([CJ_ITEM]).pages("x", "Leeds", 15, True))[0][0]
    assert again.job_id == j.job_id                                    # same ad -> same id across runs


def test_jooble_rations_lifetime_quota():
    bodies = []

    def h(req):
        bodies.append(json.loads(req.content))
        assert req.url.path == "/api/jb-key" and req.url.host == "uk.jooble.org"
        return httpx.Response(200, json={"totalCount": 500, "jobs": [
            {"id": i, "title": "Credit Controller", "company": "Northgate Foods", "location": "Leeds",
             "snippet": "x", "updated": "2026-10-20T10:00:00", "link": f"https://uk.jooble.org/jdp/{i}"} for i in range(100)]})
    c = JoobleClient("jb-key", max_requests=2, transport=httpx.MockTransport(h), min_interval=0, sleep=lambda s: None)
    pages = list(c.pages("credit controller", "Leeds", 15, True))
    assert len(bodies) == 2 and c.requests_left == 0 and len(pages) == 2   # stops at the per-run ration
    assert bodies[0]["radius"] == "26"                                     # 15 miles -> nearest allowed km value


def apify(items, runs=None):
    def h(req):
        p = req.url.path
        if p.endswith("/runs") and req.method == "POST":
            if runs is not None:
                runs.append(json.loads(req.content))
            assert req.headers["Authorization"] == "Bearer apify-token"
            return httpx.Response(201, json={"data": {"id": "r1", "status": "RUNNING", "defaultDatasetId": "d1"}})
        if p.endswith("/actor-runs/r1"):
            return httpx.Response(200, json={"data": {"id": "r1", "status": "SUCCEEDED", "defaultDatasetId": "d1"}})
        if p.endswith("/datasets/d1/items"):
            return httpx.Response(200, json=items)
        return httpx.Response(404)
    return ApifyIndeedClient("apify-token", transport=httpx.MockTransport(h), sleep=lambda s: None)


INDEED = [{"key": "abc123", "url": "https://uk.indeed.com/viewjob?jk=abc123", "title": "Management Accountant",
           "datePublished": "2026-10-01T05:00:00.000Z", "isRepost": True,
           "location": {"city": "Leeds", "latitude": 53.8, "longitude": -1.55},
           "employer": {"name": "Euro Car Parks", "corporateWebsite": "https://eurocarparks.com"},
           "baseSalary": {"min": 40000, "max": 46000, "unitOfWork": "YEAR"}, "description": {"text": "We're hiring"}},
          {"key": "def456", "url": "https://uk.indeed.com/viewjob?jk=def456", "title": "Management Accountant",
           "datePublished": "2026-10-20T05:00:00.000Z", "location": {"city": "Leeds"},
           "employer": {"name": "Harper Lane"}, "description": {"text": "Our client is a leading manufacturer"}}]


def test_apify_indeed_run_and_fields():
    runs = []
    page = list(apify(INDEED, runs).pages("management accountant", "Leeds", 15, True))[0]
    assert runs[0] == {"country": "uk", "title": "management accountant", "location": "Leeds", "limit": 300, "datePosted": "14"}
    j = page[0]
    assert j.source_says_repost and j.employer_name == "Euro Car Parks" and j.maximum_salary == 46000
    assert "description" not in j.raw and j.raw["isRepost"] is True


def test_indeed_repost_flag_makes_a_hot_lead_on_first_sight(store):
    s = store.create_search("Accountancy - Leeds", ["management accountant"], "Leeds", 15, None)
    store.q("insert into runs (search_id, status, started_at) values (%s, 'ok', now() - interval '1 hour')", (s.id,))
    r = run_search(s.id, repo=store, reed=None, settings=Settings(), extra_sources=[apify(INDEED)])
    assert r.status == "ok" and r.by_source == {"indeed": 2}
    rows = {x["employer_name"]: x for x in store.q("select employer_name, posted_by, source_says_repost from listings")}
    assert rows["Euro Car Parks"]["source_says_repost"] and rows["Euro Car Parks"]["posted_by"] == "employer"
    assert rows["Harper Lane"]["posted_by"] == "agency"                  # "Our client is..." in the ad text
    out = rebuild_hot_leads(store, threshold=2, reposts_only=True)
    assert out["hot"] == 1
    h = store.one("select * from hot_leads")
    assert h["employer_name"] == "Euro Car Parks" and h["posting_count_90d"] == 2
    # second run uses the short daily window
    runs = []
    run_search(s.id, repo=store, reed=None, settings=Settings(), extra_sources=[apify(INDEED, runs)])
    assert runs[0]["datePosted"] == "3"
    # Searches page shows volume per source
    assert store.listings_by_source()[s.id] == {"indeed": 2}
