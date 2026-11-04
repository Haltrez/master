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


def _hunter(hr_people, finder_status="valid"):
    def h(req):
        if req.url.path.endswith("domain-search"):
            people = hr_people if req.url.params["department"] == "hr" else []
            return httpx.Response(200, json={"data": {"emails": people}})
        return httpx.Response(200, json={"data": {"email": "john.smith@harlowgroup.co.uk", "score": 90,
                                                  "verification": {"status": finder_status}}})
    return Hunter("k", transport=httpx.MockTransport(h))


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
                             "email": "amy@harlowgroup.co.uk", "email_status": "valid", "email_verified": True})
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
