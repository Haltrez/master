import base64

import httpx
import pytest

from app.agencies import compliance_label, email_allowed, enrich_agency, rebuild_agencies
from app.ch_client import (CachedCompaniesHouse, CompaniesHouseClient, current_directors, name_similarity,
                           parse_officer_name)
from app.web_lookup import Brave, Hunter

CH_KEY = "ch-test-key-77"


def _listing(store, job, employer, norm, posted_by="agency", search=1, days=1, source="reed"):
    store.q("""insert into listings (job_id, source, posted_by, employer_name, employer_norm, title, title_norm, raw,
               first_seen_at, last_seen_at) values (%s, %s, %s, %s, %s, 'Accountant', 'accountant', '{}',
               now() - make_interval(days => %s), now() - make_interval(days => %s))""",
            (job, source, posted_by, employer, norm, days, days))
    store.q("insert into listing_searches values (%s, %s) on conflict do nothing", (job, search))


@pytest.fixture
def seeded(store):
    store.q("insert into searches (id, label, keywords, location) values (1, 'Accountancy - Leeds', '{a}', 'Leeds'), "
            "(2, 'Accountancy - York', '{a}', 'York')")
    for j in range(1, 5):
        _listing(store, j, "Aire Valley Recruitment Ltd", "aire valley recruitment", search=1)
    _listing(store, 5, "Aire Valley Recruitment", "aire valley recruitment", search=2)
    _listing(store, 6, "Old Agency Ltd", "old agency", days=200)
    _listing(store, 7, "Harlow Group", "harlow group", posted_by="employer")
    # the same agency ad copied on Adzuna doesn't double the count
    for j in range(1, 5):
        _listing(store, 10**15 + j, "Aire Valley Recruitment", "aire valley recruitment", source="adzuna")
    return store


def test_rebuild_agencies_counts_per_search_and_keeps_owner_fields(seeded):
    out = rebuild_agencies(seeded)
    assert out == {"agencies": 1, "new": 1}       # "Old Agency" only has ads outside the window
    ag = seeded.one("select * from agencies where name_norm = 'aire valley recruitment'")
    assert ag["ads_count_90d"] == 5 and ag["stage"] == "new"
    per = {r["search_id"]: r["ads_count"] for r in seeded.q("select * from agency_searches where agency_id = %s",
                                                            (ag["id"],))}
    assert per == {1: 4, 2: 1}                   # Reed + Adzuna copies of the same ads count once
    assert not seeded.q("select 1 from agencies where name_norm = 'harlow group'")
    seeded.update_agency(ag["id"], {"stage": "contacted", "notes": "spoke to Jo", "website": "https://ava.example",
                                    "next_follow_up": "2026-11-10"})
    rebuild_agencies(seeded)
    again = seeded.get_agency(ag["id"])
    assert (again["stage"], again["notes"], again["website"], str(again["next_follow_up"])) == (
        "contacted", "spoke to Jo", "https://ava.example", "2026-11-10")
    assert seeded.val("select count(*) from agencies where name_norm = 'aire valley recruitment'") == 1


class FakeCH:
    def __init__(self, items, profile=None, officers=None):
        self.items, self._profile, self._officers, self.calls = items, profile or {}, officers or [], []

    def search(self, name):
        self.calls.append(("search", name)); return self.items

    def profile(self, number):
        self.calls.append(("profile", number)); return self._profile

    def officers(self, number):
        self.calls.append(("officers", number)); return self._officers


OFFICERS = [
    {"name": "SMITH, John Paul", "officer_role": "director"},
    {"name": "JONES, Sarah", "officer_role": "director", "resigned_on": "2024-01-01"},
    {"name": "DOE, Jane", "officer_role": "secretary"},
    {"name": "PATEL, Dr Anil", "officer_role": "director"},
]


def _agency(store, name="Aire Valley Recruitment"):
    from app.normalise import employer_norm
    return store.one("insert into agencies (name, name_norm) values (%s, %s) returning *", (name, employer_norm(name)))


def test_enrich_known_agency_fills_type_status_and_directors(store):
    ag = _agency(store)
    ch = FakeCH([{"title": "AIRE VALLEY RECRUITMENT LIMITED", "company_number": "01234567", "company_status": "active",
                  "company_type": "ltd"}],
                profile={"type": "ltd", "company_status": "active"}, officers=OFFICERS)
    out = enrich_agency(store, ag["id"], ch)
    a = store.get_agency(ag["id"])
    assert (a["ch_company_number"], a["ch_company_type"], a["ch_company_status"]) == ("01234567", "ltd", "active")
    assert a["ch_match_confidence"] == 1.0 and not a["ch_review_needed"] and a["ch_checked_at"] is not None
    names = [(c["full_name"], c["first_name"], c["role"], c["email"]) for c in store.contacts(ag["id"])]
    assert names == [("John Paul Smith", "John", "Director", None), ("Anil Patel", "Anil", "Director", None)]
    assert out["directors_added"] == 2 and email_allowed(a)
    enrich_agency(store, ag["id"], ch)                         # re-running never duplicates contacts
    assert len(store.contacts(ag["id"])) == 2


def test_low_confidence_flagged_not_trusted(store):
    ag = _agency(store, "Aire Recruitment")
    ch = FakeCH([{"title": "AIREDALE STAFFING SOLUTIONS LTD", "company_number": "999", "company_status": "active",
                  "company_type": "ltd"}], officers=OFFICERS)
    enrich_agency(store, ag["id"], ch)
    a = store.get_agency(ag["id"])
    assert a["ch_review_needed"] and a["ch_match_confidence"] < 0.85
    assert not email_allowed(a) and "needs review" in compliance_label(a)
    assert ("officers", "999") not in ch.calls and store.contacts(ag["id"]) == []


def test_not_found_is_never_allowed(store):
    ag = _agency(store, "Bob's Recruitment")
    enrich_agency(store, ag["id"], FakeCH([]))
    a = store.get_agency(ag["id"])
    assert a["ch_company_type"] == "not_found" and not email_allowed(a)
    assert compliance_label(a) == "check before emailing (not on Companies House)"


def test_dissolved_companies_ignored_and_types_checked(store):
    ag = _agency(store, "Aire Valley Recruitment")
    enrich_agency(store, ag["id"], FakeCH([{"title": "AIRE VALLEY RECRUITMENT LTD", "company_number": "1",
                                            "company_status": "dissolved", "company_type": "ltd"}]))
    assert store.get_agency(ag["id"])["ch_company_type"] == "not_found"
    assert not email_allowed({"ch_company_type": "private-unlimited", "ch_match_confidence": 1})
    assert email_allowed({"ch_company_type": "llp", "ch_match_confidence": 0.9})
    assert not email_allowed({"ch_company_type": "ltd", "ch_match_confidence": 1, "stage": "do_not_contact"})


def test_brave_website_and_hunter_valid_only(store):
    ag = _agency(store)
    ch = FakeCH([{"title": "AIRE VALLEY RECRUITMENT LIMITED", "company_number": "1", "company_status": "active",
                  "company_type": "ltd"}], profile={"type": "ltd", "company_status": "active"},
                officers=OFFICERS[:1] + [{"name": "BROWN, Amy", "officer_role": "director"}])

    def brave_h(req):
        return httpx.Response(200, json={"web": {"results": [
            {"url": "https://uk.linkedin.com/company/aire-valley"}, {"url": "https://find-and-update.company-information.service.gov.uk/x"},
            {"url": "https://www.airevalleyrecruitment.co.uk/about"}]}})

    def hunter_h(req):
        if req.url.path.endswith("domain-search"):   # the agency's people: sales staff for the sales campaign
            v = {"status": "valid"}
            return httpx.Response(200, json={"data": {"emails": [
                {"value": "kim@airevalleyrecruitment.co.uk", "first_name": "Kim", "last_name": "Lee", "confidence": 95,
                 "position": "Senior Recruitment Consultant", "verification": v},
                {"value": "max@airevalleyrecruitment.co.uk", "first_name": "Max", "last_name": "Ray", "confidence": 90,
                 "position": "Office Administrator", "verification": v},
                {"value": "ola@airevalleyrecruitment.co.uk", "first_name": "Ola", "last_name": "Fox", "confidence": 90,
                 "position": "Recruiter", "verification": {"status": "accept_all"}}]}})
        first = req.url.params["first_name"]
        status = "valid" if first == "John" else "accept_all"
        return httpx.Response(200, json={"data": {"email": f"{first.lower()}@airevalleyrecruitment.co.uk",
                                                  "score": 90, "verification": {"status": status}}})

    brave = Brave("brave-key", transport=httpx.MockTransport(brave_h), min_interval=0)
    hunter = Hunter("hunter-key", transport=httpx.MockTransport(hunter_h))
    out = enrich_agency(store, ag["id"], ch, brave=brave, hunter=hunter)
    assert out["website"] == "airevalleyrecruitment.co.uk"
    cs = {c["first_name"]: c for c in store.contacts(ag["id"])}
    assert cs["John"]["email_verified"] and cs["John"]["email_status"] == "valid"
    assert not cs["Amy"]["email_verified"] and cs["Amy"]["email_status"] == "accept_all"
    # verified sales people only; nobody who is neither a leader nor in sales
    assert (cs["Kim"]["role"], cs["Kim"]["email_verified"], out["people_found"]) == ("Senior Recruitment Consultant", True, 1)
    assert "Max" not in cs and "Ola" not in cs


def test_suppressed_email_never_stored(store):
    ag = _agency(store)
    store.suppress("john@ava.example")
    c = store.add_contact({"agency_id": ag["id"], "full_name": "John Smith", "source": "manual",
                           "email": "John@ava.example"})
    assert c["email"] is None
    with pytest.raises(ValueError):
        store.update_contact(c["id"], {"email": "john@ava.example"})


@pytest.mark.parametrize("raw,full,first", [
    ("SMITH, John Paul", "John Paul Smith", "John"), ("O'NEILL, Mary", "Mary O'Neill", "Mary"),
    ("PATEL, Dr Anil", "Anil Patel", "Anil"), ("Jane Doe", "Jane Doe", "Jane"),
])
def test_parse_officer_name(raw, full, first):
    f, fn, _ = parse_officer_name(raw)
    assert (f, fn) == (full, first)


def test_name_similarity():
    assert name_similarity("Aire Valley Recruitment", "AIRE VALLEY RECRUITMENT LIMITED") == 1.0
    assert name_similarity("Aire Recruitment", "Airedale Staffing Solutions Ltd") < 0.85
    assert name_similarity("Smith & Jones Recruitment", "SMITH AND JONES RECRUITMENT LTD") == 1.0
    assert current_directors(OFFICERS) == [OFFICERS[0], OFFICERS[3]]


def test_ch_client_auth_throttle_and_cache(store):
    seen = []

    def h(req):
        seen.append(req)
        return httpx.Response(200, json={"items": [{"title": "X LTD"}]})

    sleeps = []
    t = {"now": 0.0}
    client = CompaniesHouseClient(CH_KEY, transport=httpx.MockTransport(h), min_interval=1.0,
                                  sleep=lambda s: (sleeps.append(s), t.__setitem__("now", t["now"] + s)),
                                  clock=lambda: t["now"])
    ch = CachedCompaniesHouse(client, store)
    assert ch.search("X") == [{"title": "X LTD"}]
    ch.search("X")
    ch.search("Y")
    assert len(seen) == 2 and sleeps == [1.0]                 # cached + throttled
    assert seen[0].headers["Authorization"] == "Basic " + base64.b64encode(f"{CH_KEY}:".encode()).decode()
