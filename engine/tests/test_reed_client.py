from __future__ import annotations

import base64
import datetime as dt
import logging

import httpx
import pytest

from app.reed_client import PAGE_SIZE, CallBudget, CallCapExceeded, ReedClient, ReedError, ReedJob
from tests.conftest import TEST_KEY, FakeReed, make_items


def client(fake: FakeReed, clock, **kw) -> ReedClient:
    return ReedClient(TEST_KEY, transport=fake.transport, sleep=clock.sleep, clock=clock, **kw)


def test_fixture_parses(sample):
    jobs = [ReedJob.from_api(i) for i in sample["results"]]
    assert len(jobs) == 5
    j = jobs[0]
    assert j.job_id == 50000001 and j.employer_name == "Harlow Group Ltd"
    assert j.posted_date == dt.date(2026, 11, 3)
    assert j.expiration_date == dt.date(2026, 12, 15)
    assert j.raw["jobTitle"].startswith("Management Accountant")
    assert jobs[2].minimum_salary is None
    assert jobs[3].expiration_date is None  # "" -> None


def test_paging_until_short_page(sample, clock):
    items = make_items(sample["results"][0], 250)
    fake = FakeReed({("accountant", "employer"): items})
    jobs = client(fake, clock).search("accountant", "Leeds", 15, None, "employer")
    assert len(jobs) == 250
    assert [int(r.url.params["resultsToSkip"]) for r in fake.requests] == [0, 100, 200]
    assert all(r.url.params["resultsToTake"] == str(PAGE_SIZE) for r in fake.requests)


def test_paging_stops_at_total_on_exact_multiple(sample, clock):
    items = make_items(sample["results"][0], 200)
    fake = FakeReed({("accountant", "employer"): items})
    jobs = client(fake, clock).search("accountant", "Leeds", 15, None, "employer")
    assert len(jobs) == 200
    assert len(fake.requests) == 2  # totalResults reached; no third empty call


def test_empty_result(clock):
    fake = FakeReed({})
    assert client(fake, clock).search("nothing", "Leeds", 15, None, "employer") == []
    assert len(fake.requests) == 1


def test_params_and_auth(sample, clock):
    fake = FakeReed({("finance manager", "agency"): sample["results"]})
    client(fake, clock).search("finance manager", "Leeds", 20, 40000, "agency")
    q = fake.requests[0].url.params
    assert q["keywords"] == "finance manager"
    assert q["locationName"] == "Leeds"
    assert q["distanceFromLocation"] == "20"
    assert q["minimumSalary"] == "40000"
    assert q["postedByDirectEmployer"] == "false"
    assert q["postedByRecruitmentAgency"] == "true"
    expected = "Basic " + base64.b64encode(f"{TEST_KEY}:".encode()).decode()
    assert fake.requests[0].headers["Authorization"] == expected
    assert TEST_KEY not in str(fake.requests[0].url)


def test_rejects_bad_posted_by(clock):
    with pytest.raises(ValueError):
        client(FakeReed({}), clock).search("x", "Leeds", 10, None, "both")  # type: ignore[arg-type]


def test_retry_429_and_5xx_then_success(sample, clock):
    fake = FakeReed({("a", "employer"): sample["results"]}, script=[429, 503, 500])
    jobs = client(fake, clock).search("a", "Leeds", 15, None, "employer")
    assert len(jobs) == 5
    assert len(fake.requests) == 4
    backoffs = [s for s in clock.sleeps if s >= 1]
    assert backoffs == [7.0, 2.0, 4.0]  # Retry-After honoured, then exponential


def test_gives_up_after_five_tries(clock):
    fake = FakeReed({}, script=[500] * 10)
    with pytest.raises(ReedError, match="after 5 tries"):
        client(fake, clock).search("a", "Leeds", 15, None, "employer")
    assert len(fake.requests) == 5


def test_401_is_not_retried(clock):
    fake = FakeReed({}, script=[401])
    with pytest.raises(ReedError, match="REED_API_KEY"):
        client(fake, clock).search("a", "Leeds", 15, None, "employer")
    assert len(fake.requests) == 1


def test_network_error_retried(sample, clock):
    calls = {"n": 0}
    ok = FakeReed({("a", "employer"): sample["results"]})

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("boom", request=request)
        return ok.handler(request)

    c = ReedClient(TEST_KEY, transport=httpx.MockTransport(handler), sleep=clock.sleep, clock=clock)
    assert len(c.search("a", "Leeds", 15, None, "employer")) == 5
    assert calls["n"] == 2


def test_throttle_one_per_second(sample, clock):
    items = make_items(sample["results"][0], 300)
    fake = FakeReed({("a", "employer"): items})
    client(fake, clock, requests_per_second=1).search("a", "Leeds", 15, None, "employer")
    # pages at skip 0,100,200; total reached -> 3 calls, 2 throttle waits of 1s
    assert len(fake.requests) == 3
    assert clock.sleeps == [1.0, 1.0]


def test_call_cap(sample, clock):
    items = make_items(sample["results"][0], 1000)
    fake = FakeReed({("a", "employer"): items})
    budget = CallBudget(3)
    with pytest.raises(CallCapExceeded):
        client(fake, clock).search("a", "Leeds", 15, None, "employer", budget=budget)
    assert len(fake.requests) == 3 and budget.used == 3


def test_retries_count_toward_cap(clock):
    fake = FakeReed({}, script=[500, 500, 500])
    with pytest.raises(CallCapExceeded):
        client(fake, clock).search("a", "Leeds", 15, None, "employer", budget=CallBudget(2))
    assert len(fake.requests) == 2


def test_logs_every_call_without_key(sample, clock, caplog):
    fake = FakeReed({("a", "employer"): sample["results"]}, script=[429])
    with caplog.at_level(logging.DEBUG):
        client(fake, clock).search("a", "Leeds", 15, None, "employer")
    lines = [r.getMessage() for r in caplog.records if r.name == "reed"]
    assert len(lines) == 2
    assert "status=429" in lines[0] and "status=200" in lines[1]
    assert "results=5" in lines[1] and "duration_ms=" in lines[1] and "keywords" in lines[1]
    assert TEST_KEY not in caplog.text


def test_get_details(clock):
    fake = FakeReed({})
    body = client(fake, clock).get_details(123)
    assert body["jobId"] == 123
    assert fake.requests[0].url.path.endswith("/jobs/123")


def test_missing_key():
    with pytest.raises(ReedError):
        ReedClient("")
