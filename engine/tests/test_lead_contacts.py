import csv
import datetime as dt
import io

import httpx
import pytest

from app.lead_contacts import email_for_export, enrich_lead_company, pick_hunter_person
from app.leads import lead_rows, posted_since
from app.web_lookup import Brave, Hunter
from tests.test_agencies import OFFICERS, FakeCH
from tests.test_api import H, api, calls, seed  # noqa: F401  (fixtures)


def _brave():
    return Brave("k", transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"web": {"results": [
        {"url": "https://www.linkedin.com/company/harlow"}, {"url": "https://www.harlowgroup.co.uk/about"}]}})), min_interval=0)


def _hunter(hr_people, finder_status="valid", calls=None, **kw):
    def h(req):
        if calls is not None:
            calls.append(req.url.path)
        if req.url.path.endswith("domain-search"):
            return httpx.Response(200, json={"data": {"emails": hr_people}})
        return httpx.Response(200, json={"data": {"email": "john.smith@harlowgroup.co.uk", "score": 90,
                                                  "verification": {"status": finder_status}}})
    return Hunter("k", transport=httpx.MockTransport(h), **kw)


CH_LTD = [{"title": "HARLOW GROUP LIMITED", "company_number": "0099", "company_status": "active", "company_type": "ltd"}]


def test_hr_person_preferred_over_director(store):
    hr = [{"value": "amy.brown@harlowgroup.co.uk", "first_name": "Amy", "last_name": "Brown", "position": "Talent Acquisition Manager",
           "confidence": 92, "verification": {"status": "valid"}},
          {"value": "ceo@harlowgroup.co.uk", "first_name": "Bob", "last_name": "Big", "position": "CEO", "confidence": 99,
           "verification": {"status": "valid"}}]
    r = enrich_lead_company(store, "Harlow Group Ltd", "harlow group", FakeCH(CH_LTD, officers=OFFICERS), _brave(), _hunter(hr))
    assert (r["contact_name"], r["contact_role"], r["email"], r["email_verified"]) == (
        "Amy Brown", "Talent Acquisition Manager", "amy.brown@harlowgroup.co.uk", True)
    assert r["website"] == "https://harlowgroup.co.uk" and r["ch_company_type"] == "ltd"


def test_falls_back_to_director_email_and_keeps_unverified_out_of_exports(store):
    r = enrich_lead_company(store, "Harlow Group Ltd", "harlow group", FakeCH(CH_LTD, officers=OFFICERS), _brave(),
                            _hunter([], finder_status="accept_all"))
    assert r["contact_name"] == "John Paul Smith" and r["contact_role"] == "Director"
    assert r["email"] == "john.smith@harlowgroup.co.uk" and not r["email_verified"]
    assert email_for_export(store.lead_contacts_for(["harlow group"])["harlow group"]) == ""


def test_no_companies_house_match_keeps_no_email(store):
    r = enrich_lead_company(store, "Bob's Plumbing", "bobs plumbing", FakeCH([]), None, None)
    assert r["ch_company_type"] == "not_found" and not r.get("contact_name")
    assert email_for_export({"email": "x@y.z", "email_verified": True, "ch_company_type": "not_found"}) == ""
    assert email_for_export({"email": "x@y.z", "email_verified": True, "ch_company_type": "llp"}) == "x@y.z"


def test_suppressed_email_never_used(store):
    store.suppress("amy.brown@harlowgroup.co.uk")
    hr = [{"value": "amy.brown@harlowgroup.co.uk", "first_name": "Amy", "last_name": "Brown", "position": "HR Manager",
           "confidence": 92, "verification": {"status": "valid"}}]
    r = enrich_lead_company(store, "Harlow Group Ltd", "harlow group", FakeCH(CH_LTD, officers=OFFICERS), _brave(), _hunter(hr))
    assert r["email"] != "amy.brown@harlowgroup.co.uk"


def test_pick_hunter_person():
    assert pick_hunter_person([{"verified": False, "first_name": "A", "position": "HR", "confidence": 99}]) is None


def test_posted_since():
    assert posted_since({"first_seen_at": dt.datetime(2026, 8, 12), "posting_count_90d": 4}) == "4 times since August 2026"
    assert posted_since({"first_seen_at": dt.datetime(2026, 8, 12), "posting_count_90d": 1}) == "since August 2026"


def _listings_for_seeded_lead(store):
    for jid, days, url, sal in [(1, 40, "https://www.reed.co.uk/jobs/x/old", 42000), (2, 2, "https://www.reed.co.uk/jobs/x/1", 45000)]:
        store.q("""insert into listings (job_id, posted_by, employer_name, employer_norm, title, title_norm, url, max_salary, raw,
                   first_seen_at, last_seen_at) values (%s, 'employer', 'Harlow Group', 'harlow group', 'Management Accountant',
                   'management accountant', %s, %s, '{}', now() - make_interval(days => %s), now())""", (jid, url, sal, days))
    store.q("update hot_leads set job_ids = '{1,2}' where lead_key = 'key1'")


def test_lead_rows_and_csv_export(api, store):
    seed(store)
    _listings_for_seeded_lead(store)
    store.save_lead_contact({"employer_norm": "harlow group", "company_name": "HARLOW GROUP LIMITED", "ch_company_type": "ltd",
                             "contact_name": "Amy Brown", "contact_first_name": "Amy", "contact_role": "HR Manager",
                             "email": "amy@harlowgroup.co.uk", "email_status": "valid", "email_verified": True,
                             "verified_company": True, "company_status": "active", "contacts_checked": True})
    r = api.get("/exports/hot-leads.csv", headers=H, params={"search_id": 1})
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    rows = list(csv.DictReader(io.StringIO(r.text.lstrip("﻿"))))
    harlow = next(x for x in rows if x["Company"] == "Harlow Group")
    assert harlow["Previous ad"] == "https://www.reed.co.uk/jobs/x/old" and harlow["Max salary"] == "45000"
    assert (harlow["Decision maker"], harlow["Their role"], harlow["Email"]) == ("Amy Brown", "HR Manager", "amy@harlowgroup.co.uk")
    assert harlow["Suggested email"].startswith("Hi Amy,")
    assert "has advertised the Management Accountant role 4 times since August 2026" in harlow["Suggested email"]
    other = next(x for x in rows if x["Company"] == "Kirkstall Engineering")
    assert other["Email"] == "" and other["Suggested email"].startswith("Hello,")
    # hot leads JSON carries the contact for the CRM table
    j = api.get("/hot-leads", headers=H).json()
    assert next(x for x in j if x["employer_name"] == "Harlow Group")["contact"]["name"] == "Amy Brown"


def test_daily_leads_have_the_sheet_columns(api, store):
    a, _ = seed(store)
    _listings_for_seeded_lead(store)
    c = api.post("/clients", headers=H, json={"agency_id": a["id"], "search_ids": [1]}).json()
    leads = api.get(f"/clients/{c['id']}/daily-leads", headers=H).json()["leads"]
    for k in ("date_added", "company", "role", "max_salary", "times_posted", "first_seen", "location", "ad_link",
              "previous_ad_link", "decision_maker", "email", "suggested_email"):
        assert k in leads[0]


def test_manual_contact_edit_and_suppression(api, store):
    seed(store)
    r = api.patch("/lead-contacts/harlow group", headers=H, json={"contact_name": "Jo Bloggs", "email": "jo@harlow.co.uk",
                                                                  "email_verified": True})
    assert r.status_code == 200 and r.json()["contact_name"] == "Jo Bloggs"
    store.suppress("no@harlow.co.uk")
    assert api.patch("/lead-contacts/harlow group", headers=H, json={"email": "no@harlow.co.uk"}).status_code == 409



# --- strict filters: real companies only, recruitment businesses removed ---------------------

def test_recruitment_sic_marks_company_as_agency_and_removes_its_leads(store):
    from app.lead_contacts import verify_lead_company
    store.q("""insert into hot_leads (lead_key, employer_name, employer_norm, title, posting_count_90d, first_seen_at,
               last_seen_at, live_now, long_open, score, job_ids) values ('k1', 'Harper Lane Ltd', 'harper lane',
               'Accountant', 3, now(), now(), true, false, 5, '{1}')""")
    store.q("""insert into listings (job_id, posted_by, employer_name, employer_norm, title, title_norm, raw)
               values (1, 'employer', 'Harper Lane Ltd', 'harper lane', 'Accountant', 'accountant', '{}')""")
    ch = FakeCH([{"title": "HARPER LANE LIMITED", "company_number": "11", "company_status": "active", "company_type": "ltd"}],
                profile={"type": "ltd", "company_status": "active", "sic_codes": ["78109"]})
    r = verify_lead_company(store, "Harper Lane Ltd", "harper lane", ch)
    assert r["is_agency"] and not r["verified_company"]
    assert store.val("select count(*) from hot_leads") == 0
    assert store.one("select posted_by from listings")["posted_by"] == "agency"
    assert store.get_overrides() == {"harper lane": True}          # stays an agency in every future run


def test_real_company_is_verified_and_then_gets_contacts(store):
    from app.lead_contacts import enrich_new_lead_companies, verify_lead_company
    ch = FakeCH(CH_LTD, profile={"type": "ltd", "company_status": "active", "sic_codes": ["70100"]}, officers=OFFICERS)
    r = verify_lead_company(store, "Harlow Group Ltd", "harlow group", ch)
    assert r["verified_company"] and not r["is_agency"] and r["sic_codes"] == ["70100"]
    dissolved = FakeCH([{"title": "OLD CO LTD", "company_number": "2", "company_status": "active", "company_type": "ltd"}],
                       profile={"type": "ltd", "company_status": "dissolved"})
    assert not verify_lead_company(store, "Old Co Ltd", "old co", dissolved)["verified_company"]
    assert not verify_lead_company(store, "Bob's Cafe", "bobs cafe", FakeCH([]))["verified_company"]


def test_strict_mode_keeps_unverified_out_of_sheets_until_confirmed(api, store):
    a, _ = seed(store)
    store.q("update lead_contacts set verified_company = false where employer_norm = 'northgate foods'")
    c = api.post("/clients", headers=H, json={"agency_id": a["id"], "search_ids": [1]}).json()
    got = api.get(f"/clients/{c['id']}/daily-leads", headers=H, params={"mark": True}).json()["leads"]
    assert {l["company"] for l in got} == {"Harlow Group", "Kirkstall Engineering"}
    # confirmed later -> delivered then (it was never marked as delivered while unverified)
    store.q("update lead_contacts set verified_company = true where employer_norm = 'northgate foods'")
    got = api.get(f"/clients/{c['id']}/daily-leads", headers=H, params={"mark": True}).json()["leads"]
    assert [l["company"] for l in got] == ["Northgate Foods"]
    # turning strict off includes unchecked companies
    api.patch("/settings", headers=H, json={"verified_companies_only": False})
    csv_rows = list(csv.DictReader(io.StringIO(api.get("/exports/hot-leads.csv", headers=H).text.lstrip("\ufeff"))))
    assert len(csv_rows) == 3


def test_hot_leads_json_shows_company_check(api, store):
    seed(store)
    store.q("update lead_contacts set verified_company = false, ch_company_type = 'not_found' where employer_norm = 'harlow group'")
    rows = {r["employer_name"]: r for r in api.get("/hot-leads", headers=H).json()}
    assert rows["Harlow Group"]["company_check"] == "not_found" and not rows["Harlow Group"]["company_verified"]
    assert rows["Kirkstall Engineering"]["company_verified"]


def test_hunter_spends_at_most_two_credits_and_stops_at_the_monthly_limit(store):
    calls = []
    enrich_lead_company(store, "Harlow Group Ltd", "harlow group", FakeCH(CH_LTD, officers=OFFICERS), _brave(),
                        _hunter([], finder_status="accept_all", calls=calls))
    assert len(calls) == 2   # one domain search + one email finder for the first director
    calls.clear()
    h = _hunter([], calls=calls, store=store, monthly_limit=3)
    for _ in range(5):
        h.domain_people("harlowgroup.co.uk")
    assert len(calls) == 3


def test_paid_lookups_only_for_companies_in_open_orders(store):
    from app import orders as O
    store.q("insert into searches (id, label, keywords, location) values (50, 'Other - York', '{x}', 'York')")
    for key, emp, sid in (("a", "In Order Ltd", None), ("b", "No Order Ltd", 50)):
        store.q("""insert into hot_leads (lead_key, employer_name, employer_norm, title, posting_count_90d, first_seen_at,
                   last_seen_at, live_now, long_open, score, job_ids) values (%s, %s, %s, 'Developer', 3, now(), now(),
                   true, false, 5, '{}')""", (key, emp, emp.lower()))
        store.q("insert into lead_contacts (employer_norm, company_name, verified_company) values (%s, %s, true)",
                (emp.lower(), emp))
    o = O.create_order(store, agency_name="Acme", industry="IT & Tech", location="Leeds", leads_wanted=5)
    store.q("insert into hot_lead_searches values ('a', %s), ('b', 50)", (o["search_id"],))
    assert [r["employer_name"] for r in store.lead_companies_needing_contacts(10)] == ["In Order Ltd"]
    O.update_order(store, o["id"], {"status": "paused"})
    assert store.lead_companies_needing_contacts(10) == []
