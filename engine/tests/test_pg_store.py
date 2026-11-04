"""Phase 1 collector + Role Match running on the real Postgres store."""

import datetime as dt

from app.collect import run_search
from app.config import Settings
from app.reed_client import ReedClient
from tests.conftest import TEST_KEY, FakeReed, make_items


def reed_for(fake):
    return ReedClient(TEST_KEY, transport=fake.transport, requests_per_second=0, sleep=lambda s: None)


def test_collector_end_to_end_on_postgres(store, sample):
    s = store.create_search("Accountancy - Leeds", ["management accountant"], "Leeds", 15, None)
    res = sample["results"]
    fake = FakeReed({("management accountant", "employer"): res[:4],
                     ("management accountant", "agency"): [res[4]]})
    settings = Settings(reed_api_key=TEST_KEY, reed_requests_per_second=0)
    r1 = run_search(s.id, repo=store, reed=reed_for(fake), settings=settings)
    assert (r1.status, r1.api_calls, r1.listings_seen, r1.new_listings) == ("ok", 2, 5, 5)
    store.q("update listings set first_seen_at = first_seen_at - interval '1 day', "
            "last_seen_at = last_seen_at - interval '1 day'")
    r2 = run_search(s.id, repo=store, reed=reed_for(fake), settings=settings)
    assert (r2.status, r2.new_listings) == ("ok", 0)
    rows = store.q("select job_id, posted_by, first_seen_at < last_seen_at as moved from listings order by job_id")
    assert len(rows) == 5 and all(r["moved"] for r in rows)
    assert {r["job_id"]: r["posted_by"] for r in rows}[50000003] == "agency"  # heuristic
    run = store.get_run(r2.run_id)
    assert run["status"] == "ok" and run["finished_at"] is not None
    # direct employers joined the employer directory (trigger)
    assert store.one("select status from employers where name_norm = 'harlow group'")["status"] == "research"


def test_role_repo_contract_on_postgres(store):
    assert len(store.families()) == 6 and len(store.employers()) == 3
    e = store.add_employer({"name": "Acme M&E Ltd", "source": "manual", "town": "Bolton"})
    again = store.add_employer({"name": "ACME M&E Limited", "careers_url": "https://acme/careers", "town": "Leeds"})
    assert again["id"] == e["id"] and again["careers_url"] == "https://acme/careers" and again["town"] == "Bolton"
    j = store.upsert_job({"employer_id": e["id"], "source": "careers_page", "title": "Electrical Estimator",
                          "title_norm": "electrical estimator", "location_text": None, "url": "https://acme/1",
                          "date_posted": dt.date(2026, 9, 1)})
    store.q("update employer_jobs set first_seen_at = now() - interval '3 days', last_seen_at = now() - interval '3 days'")
    j2 = store.upsert_job({"employer_id": e["id"], "source": "careers_page", "title": "Electrical Estimator",
                           "title_norm": "electrical estimator", "location_text": None, "url": "https://acme/1",
                           "date_posted": None})
    assert j2["id"] == j["id"] and j2["date_posted"] == dt.date(2026, 9, 1) and j2["last_seen_at"] > j2["first_seen_at"]
    ag = store.create_agency("MCM Recruitment Solutions")
    b = store.save_brief({"agency_id": ag["id"], "role_family_id": 1, "label": "EE - Warrington",
                          "location": "Warrington", "radius_miles": 30, "sector_keywords": ["m&e"]})
    b2 = store.save_brief({"agency_id": ag["id"], "role_family_id": 1, "label": "EE - Warrington",
                           "location": "warrington", "radius_miles": 40, "sector_keywords": []})
    assert b2["id"] == b["id"] and b2["radius_miles"] == 40
    m = store.upsert_match({"brief_id": b["id"], "employer_job_id": j["id"], "score": 6, "reasons": ["x"],
                            "distance_miles": 3.2})
    store.q("update brief_matches set status = 'sent'")
    m2 = store.upsert_match({"brief_id": b["id"], "employer_job_id": j["id"], "score": 7, "reasons": ["y"],
                             "distance_miles": 3.2})
    assert m2["id"] == m["id"] and m2["status"] == "sent" and m2["score"] == 7
    store.cache_set("k", {"a": 1})
    assert store.cache_get("k") == {"a": 1} and store.cache_get("k", max_age_days=0) is None
    store.suppress("Jo@Example.com")
    assert store.is_suppressed("jo@example.com ") and not store.is_suppressed("x@example.com")
    assert store.setting("daily_outreach_target") == 100
