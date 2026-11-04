import json

import httpx

from app.collect import run_search
from app.config import Settings
from app.theirstack_client import ID_OFFSET, TheirStackClient
from tests.conftest import TEST_KEY


def job(i, company="Harlow Group Ltd", title="Management Accountant", lat=53.80, lng=-1.55, loc="Leeds, England"):
    return {"id": 9000 + i, "job_title": title, "company": company, "company_domain": "harlowgroup.co.uk",
            "short_location": loc, "latitude": lat, "longitude": lng, "date_posted": "2026-09-01",
            "final_url": f"https://www.indeed.co.uk/viewjob?jk={i}", "min_annual_salary": 40000,
            "max_annual_salary": 45000, "description": "long text", "hiring_team": [{"full_name": "Someone"}]}


class FakeTS:
    def __init__(self, items, reject_company_type=False):
        self.items, self.reject, self.bodies = items, reject_company_type, []

    def handler(self, req):
        body = json.loads(req.content)
        self.bodies.append(body)
        assert req.headers["Authorization"] == "Bearer ts-key"
        if self.reject and "company_type" in body:
            return httpx.Response(422, json={"detail": "extra field"})
        start = body["page"] * body["limit"]
        return httpx.Response(200, json={"data": self.items[start:start + body["limit"]], "metadata": {}})


def client(fake):
    return TheirStackClient("ts-key", transport=httpx.MockTransport(fake.handler), min_interval=0, sleep=lambda s: None)


def test_paging_cap_and_fields():
    fake = FakeTS([job(i) for i in range(60)])
    pages = list(client(fake).iter_search_pages(["management accountant"], max_age_days=90, max_jobs=40))
    assert sum(len(p) for p in pages) == 40                       # hard cap = credits spent
    assert [b["limit"] for b in fake.bodies] == [25, 15]
    b = fake.bodies[0]
    assert b["job_country_code_or"] == ["GB"] and b["posted_at_max_age_days"] == 90 and b["company_type"] == "direct_employer"
    j = pages[0][0]
    assert j.job_id == ID_OFFSET + 9000 and j.employer_name == "Harlow Group Ltd" and j.maximum_salary == 45000
    assert "description" not in j.raw and "hiring_team" not in j.raw     # no free text or people stored


def test_company_type_rejected_retries_without_it():
    fake = FakeTS([job(1)], reject_company_type=True)
    pages = list(client(fake).iter_search_pages(["x"], max_age_days=3, max_jobs=10))
    assert len(pages[0]) == 1 and "company_type" not in fake.bodies[-1]


def test_collector_keeps_nearby_jobs_and_backfills_once(store):
    s = store.create_search("Accountancy - Leeds", ["management accountant"], "Leeds", 15, None)
    items = [job(1), job(2, lat=51.5, lng=-0.12, loc="London"), job(3, company="Leeds Finance Recruitment"),
             job(4, lat=None, lng=None, loc="Leeds")]
    fake = FakeTS(items)

    class Geo:
        def geocode(self, text):
            from app.geo import Place
            return Place(53.7997, -1.5492, "Leeds")

    settings = Settings(theirstack_api_key="ts-key", theirstack_max_jobs_per_run=50)
    r = run_search(s.id, repo=store, reed=None, settings=settings, theirstack=client(fake), geocoder=Geo())
    assert r.status == "ok" and r.theirstack_jobs == 4
    rows = {x["job_id"] - ID_OFFSET: x for x in store.q("select job_id, source, posted_by from listings")}
    assert set(rows) == {9001, 9003, 9004}                       # London job dropped
    assert rows[9003]["posted_by"] == "agency" and rows[9001]["source"] == "theirstack"
    assert fake.bodies[0]["posted_at_max_age_days"] == 90
    run_search(s.id, repo=store, reed=None, settings=settings, theirstack=client(fake), geocoder=Geo())
    assert fake.bodies[-1]["posted_at_max_age_days"] == 3        # history pulled only on the first run
