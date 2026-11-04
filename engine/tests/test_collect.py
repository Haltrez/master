from __future__ import annotations

import datetime as dt
import logging

import pytest

from app.collect import run_all, run_search
from app.config import Settings
from app.db import MemoryRepo
from app.reed_client import ReedClient
from tests.conftest import TEST_KEY, FakeReed, make_items


class StepClock:
    def __init__(self):
        self.t = dt.datetime(2026, 11, 3, 6, 0, tzinfo=dt.timezone.utc)

    def __call__(self):
        return self.t

    def advance(self, **kw):
        self.t += dt.timedelta(**kw)


@pytest.fixture
def settings():
    return Settings(reed_api_key=TEST_KEY, reed_requests_per_second=0)


@pytest.fixture
def repo():
    r = MemoryRepo(clock=StepClock())
    r.create_search("Accountancy - Leeds", ["management accountant", "finance manager"], "Leeds", 15, None)
    return r


def datasets(sample):
    res = sample["results"]
    employer = [res[0], res[1], res[2], res[3]]  # res[2] is an agency-named employer result
    agency = [res[4]] + make_items(res[4], 3, start_id=80000000, employerName="Leeds Finance Appointments Ltd")
    return {
        ("management accountant", "employer"): employer,
        ("management accountant", "agency"): agency,
        # finance manager returns one overlapping employer job and nothing else
        ("finance manager", "employer"): [res[0]],
    }


def reed_for(fake):
    return ReedClient(TEST_KEY, transport=fake.transport, requests_per_second=0, sleep=lambda s: None)


def test_end_to_end_counts(sample, repo, settings):
    fake = FakeReed(datasets(sample))
    r = run_search(1, repo=repo, reed=reed_for(fake), settings=settings)
    assert r.status == "ok", r.error
    # 2 keywords x 2 queries, each a single short page
    assert r.api_calls == len(fake.requests) == 4
    assert r.listings_seen == 8  # 4 employer-query + 4 agency-query, overlap de-duplicated
    assert r.new_listings == 8
    run = repo.get_run(r.run_id)
    assert run["status"] == "ok" and run["finished_at"] is not None
    assert (run["api_calls"], run["listings_seen"], run["new_listings"]) == (4, 8, 8)
    assert repo.searches[1].last_run_at is not None
    assert {(j, 1) for j in repo.listings} == repo.listing_searches


def test_second_run_no_duplicates_and_moves_last_seen(sample, repo, settings):
    fake = FakeReed(datasets(sample))
    run_search(1, repo=repo, reed=reed_for(fake), settings=settings)
    first = {j: (l["first_seen_at"], l["last_seen_at"]) for j, l in repo.listings.items()}
    repo.clock.advance(days=1)
    r2 = run_search(1, repo=repo, reed=reed_for(fake), settings=settings)
    assert r2.status == "ok"
    assert r2.new_listings == 0 and r2.listings_seen == 8
    assert len(repo.listings) == 8
    for j, l in repo.listings.items():
        assert l["first_seen_at"] == first[j][0]
        assert l["last_seen_at"] > first[j][1]


def test_posted_by_from_query_and_heuristic(sample, repo, settings, caplog):
    fake = FakeReed(datasets(sample))
    with caplog.at_level(logging.INFO):
        r = run_search(1, repo=repo, reed=reed_for(fake), settings=settings)
    pb = {j: l["posted_by"] for j, l in repo.listings.items()}
    assert pb[50000001] == "employer"  # Harlow Group Ltd
    assert pb[50000002] == "employer"
    assert pb[50000004] == "employer"
    assert pb[50000003] == "agency"    # "Aire Valley Finance Recruitment" via employer query
    assert pb[50000005] == "agency"    # agency query
    assert all(pb[j] == "agency" for j in range(80000000, 80000003))
    assert r.reclassified == 1
    assert "reclassified 'Aire Valley Finance Recruitment'" in caplog.text
    assert r.by_posted_by == {"employer": 3, "agency": 5}


def test_overrides_win_both_ways(sample, repo, settings):
    repo.overrides = {"harlow group": True, "aire valley finance recruitment": False}
    fake = FakeReed(datasets(sample))
    run_search(1, repo=repo, reed=reed_for(fake), settings=settings)
    assert repo.listings[50000001]["posted_by"] == "agency"
    assert repo.listings[50000003]["posted_by"] == "employer"


def test_override_applies_on_later_run(sample, repo, settings):
    fake = FakeReed(datasets(sample))
    run_search(1, repo=repo, reed=reed_for(fake), settings=settings)
    assert repo.listings[50000001]["posted_by"] == "employer"
    repo.overrides = {"harlow group": True}
    run_search(1, repo=repo, reed=reed_for(fake), settings=settings)
    assert repo.listings[50000001]["posted_by"] == "agency"


def test_norm_columns_filled(sample, repo, settings):
    run_search(1, repo=repo, reed=reed_for(FakeReed(datasets(sample))), settings=settings)
    l = repo.listings[50000001]
    assert l["employer_norm"] == "harlow group"
    assert l["title_norm"] == "management accountant"
    assert l["location_norm"] == "leeds"
    assert l["posted_date"] == "2026-11-03"
    assert l["raw"]["jobId"] == 50000001


def test_call_cap_marks_run_error_but_keeps_listings(sample, repo):
    big = make_items(sample["results"][0], 500)
    fake = FakeReed({("management accountant", "employer"): big})
    s = Settings(reed_api_key=TEST_KEY, reed_requests_per_second=0, reed_max_calls_per_run=2)
    r = run_search(1, repo=repo, reed=reed_for(fake), settings=s)
    assert r.status == "error" and "cap" in r.error
    assert r.api_calls == 2 and r.new_listings == 200 == len(repo.listings)
    assert repo.get_run(r.run_id)["status"] == "error"


def test_reed_failure_closes_run_with_error(repo, settings):
    fake = FakeReed({}, script=[401])
    r = run_search(1, repo=repo, reed=reed_for(fake), settings=settings)
    assert r.status == "error" and "401" in r.error
    assert repo.get_run(r.run_id)["finished_at"] is not None


def test_unknown_search(repo, settings):
    with pytest.raises(ValueError):
        run_search(99, repo=repo, reed=reed_for(FakeReed({})), settings=settings)


def test_run_all_runs_active_only(sample, repo, settings):
    repo.create_search("Inactive", ["x"], "York", 10, None)
    repo.searches[2].active = False
    repo.create_search("Finance - Bradford", ["finance manager"], "Bradford", 10, None)
    results = run_all(repo=repo, reed=reed_for(FakeReed(datasets(sample))), settings=settings)
    assert [r.search_id for r in results] == [1, 3]
    assert all(r.status == "ok" for r in results)



@pytest.mark.parametrize("text,agency", [
    ("Our client is a leading manufacturer based in Leeds...", True),
    ("We are recruiting on behalf of our client, a growing firm.", True),
    ("My client, a national retailer, seeks a Finance Manager.", True),
    ("We are exclusively partnered with a leading housebuilder.", True),
    ("Harlow Group is hiring a Management Accountant to join our team.", False),
    ("Our clients are at the heart of everything we do as a law firm.", False),   # an employer talking about customers
])
def test_agency_wording_in_ad_text(sample, text, agency):
    from app.collect import agency_name_pattern, classify
    from app.config import DEFAULT_AGENCY_NAME_WORDS
    from app.reed_client import ReedJob
    item = {**sample["results"][0], "employerName": "Harper Lane Ltd", "jobDescription": text}
    pb, _ = classify(ReedJob.from_api(item), "employer", {}, agency_name_pattern(DEFAULT_AGENCY_NAME_WORDS))
    assert (pb == "agency") is agency



@pytest.mark.parametrize("kw,title,ok", [
    ("management accountant", "Financial/Management Accountant", True),
    ("management accountant", "Management Accountant (Hybrid)", True),
    ("management accountant", "Account Manager", False),
    ("management accountant", "Accounts Payable Manager", False),
    ("electrical estimator", "Senior Electrical Estimator", True),
    ("m&e project manager", "M&E Project Manager - Leeds", True),
    ("software engineer", "Software Engineering Manager", True),
])
def test_title_matches(kw, title, ok):
    from app.collect import title_matches
    assert title_matches(kw, title) is ok



def test_one_source_failing_never_stops_the_others_and_status_is_saved(store, sample):
    from app.collect import run_search
    from app.config import Settings
    from app.reed_client import ReedClient, ReedError
    from tests.conftest import FakeReed

    class Broken:
        name = "jooble"

        def pages(self, *a):
            raise ReedError("Jooble refused the key (403)")
            yield []

    s = store.create_search("A", ["management accountant"], "Leeds", 15, None)
    reed = ReedClient(TEST_KEY, transport=FakeReed({("management accountant", "employer"): sample["results"][:2]}).transport,
                      requests_per_second=0, sleep=lambda x: None)
    r = run_search(s.id, repo=store, reed=reed, settings=Settings(reed_api_key=TEST_KEY, reed_requests_per_second=0),
                   extra_sources=[Broken()])
    assert r.status == "ok" and r.by_source["reed"] == 2
    assert r.source_errors == {"jooble": "Jooble refused the key (403)"}
    assert store.cache_get("source_status:reed")["ok"] is True and store.cache_get("source_status:reed")["ads"] == 2
    assert store.cache_get("source_status:jooble") == {**store.cache_get("source_status:jooble"), "ok": False,
                                                       "error": "Jooble refused the key (403)"}
