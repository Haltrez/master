import httpx

from app.adzuna_client import ID_OFFSET, AdzunaClient, adzuna_job_id
from app.collect import run_search
from app.config import Settings
from app.db import MemoryRepo
from app.reed_client import CallBudget, ReedClient
from tests.conftest import TEST_KEY, FakeReed


def item(i, company="Harlow Group Ltd", title="Management Accountant"):
    return {"id": str(4000000000 + i), "title": title, "company": {"display_name": company},
            "location": {"display_name": "Leeds, West Yorkshire"}, "created": "2026-10-01T09:00:00Z",
            "redirect_url": f"https://www.adzuna.co.uk/jobs/land/ad/{4000000000 + i}", "salary_min": 40000}


class FakeAdzuna:
    def __init__(self, items, fail=False):
        self.items, self.fail, self.requests = items, fail, []

    def handler(self, req):
        self.requests.append(req)
        if self.fail:
            return httpx.Response(500)
        page = int(req.url.path.rsplit("/", 1)[-1])
        per = int(req.url.params["results_per_page"])
        chunk = self.items[(page - 1) * per: page * per]
        return httpx.Response(200, json={"count": len(self.items), "results": chunk})


def client(fake):
    return AdzunaClient("app-id-123", "secret-key-456", transport=httpx.MockTransport(fake.handler),
                        min_interval=0, sleep=lambda s: None)


def test_paging_namespacing_and_params(caplog):
    fake = FakeAdzuna([item(i) for i in range(120)])
    pages = list(client(fake).iter_search_pages("management accountant", "Leeds", 15, budget=CallBudget(10)))
    assert [len(p) for p in pages] == [50, 50, 20]
    j = pages[0][0]
    assert j.job_id == ID_OFFSET + 4000000000 == adzuna_job_id("4000000000")
    assert j.job_id > 10**12   # never collides with Reed's ids
    q = fake.requests[0].url.params
    assert (q["title_only"], q["where"], q["distance"]) == ("management accountant", "Leeds", "24")  # 15 miles in km
    assert q["sort_by"] == "date" and "what" not in q
    assert "secret-key-456" not in caplog.text


def test_collector_classifies_adzuna_agencies_and_survives_adzuna_failure(sample):
    repo = MemoryRepo()
    repo.create_search("Accountancy - Leeds", ["management accountant"], "Leeds", 15, None)
    fake = FakeAdzuna([item(1), item(2, company="Hays Accountancy & Finance"), item(3, company="Michael Page"),
                       item(4, company="Leeds Finance Recruitment")])
    reed = ReedClient(TEST_KEY, transport=FakeReed({}).transport, requests_per_second=0, sleep=lambda s: None)
    s = Settings(reed_api_key=TEST_KEY, reed_requests_per_second=0)
    r = run_search(1, repo=repo, reed=reed, settings=s, adzuna=client(fake))
    assert r.status == "ok"
    pb = {l["employer_name"]: (l["posted_by"], l["source"]) for l in repo.listings.values()}
    assert pb["Harlow Group Ltd"] == ("employer", "adzuna")
    assert pb["Michael Page"][0] == "agency"                 # known brand
    assert pb["Leeds Finance Recruitment"][0] == "agency"    # name word
    assert pb["Hays Accountancy & Finance"][0] == "agency"   # brand + division word

    r2 = run_search(1, repo=repo, reed=reed, settings=s, adzuna=client(FakeAdzuna([], fail=True)))
    assert r2.status == "ok"     # Adzuna down: Reed results still count, run still ok


def test_after_run_hook_runs_and_errors_are_contained(sample):
    repo = MemoryRepo()
    repo.create_search("A", ["x"], "Leeds", 15, None)
    reed = ReedClient(TEST_KEY, transport=FakeReed({}).transport, requests_per_second=0, sleep=lambda s: None)
    s = Settings(reed_api_key=TEST_KEY, reed_requests_per_second=0)
    assert run_search(1, repo=repo, reed=reed, settings=s, after_run=lambda r: {"hot": 2}).after_run == {"hot": 2}
    boom = run_search(1, repo=repo, reed=reed, settings=s, after_run=lambda r: 1 / 0)
    assert boom.status == "ok" and "error" in boom.after_run


def test_known_agency_brand_matching():
    from app.collect import is_known_agency
    from app.config import KNOWN_AGENCY_BRANDS
    k = set(KNOWN_AGENCY_BRANDS)
    assert is_known_agency("hays", k) and is_known_agency("hays accountancy and finance", k)
    assert is_known_agency("michael page engineering", k)
    assert not is_known_agency("hays travel", k)
    assert not is_known_agency("reed smith", k)
    assert not is_known_agency("harlow group", k)



def test_first_run_30_days_then_3(store):
    from app.collect import run_search
    s = store.create_search("Accountancy - Leeds", ["management accountant"], "Leeds", 15, None)
    fake = FakeAdzuna([item(1)])
    settings = Settings()
    run_search(s.id, repo=store, reed=None, settings=settings, adzuna=client(fake))
    run_search(s.id, repo=store, reed=None, settings=settings, adzuna=client(fake))
    assert [r.url.params["max_days_old"] for r in fake.requests] == ["30", "3"]
