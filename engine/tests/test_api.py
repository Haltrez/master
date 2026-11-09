"""Engine API against the real test database, with collection/enrichment faked."""

import csv
import datetime as dt
import io
import json
import time

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import Hooks, create_app

SECRET = "test-engine-secret"
H = {"X-API-Key": SECRET}


class Calls:
    def __init__(self):
        self.collect_one, self.collect_all, self.enrich, self.webhooks = [], [], [], []


@pytest.fixture
def calls():
    return Calls()


@pytest.fixture
def api(store, calls):
    def collect_one(s, st, sid, rid):
        calls.collect_one.append((sid, rid))
        s.finish_run(rid, status="ok", api_calls=2, listings_seen=10, new_listings=10, error=None)

    def collect_all(s, st, rids):
        time.sleep(0.01)
        calls.collect_all.append(rids)
        for rid in rids.values():
            s.finish_run(rid, status="ok", api_calls=1, listings_seen=1, new_listings=1, error=None)

    def enrich(s, st, aid):
        calls.enrich.append(aid)
        return {"agency_id": aid, "companies_house": "matched"}

    def role_services(s, st, need):
        from app.role_match import Services
        return Services(repo=s, settings=st, geocoder=None)

    hooks = Hooks(collect_one=collect_one, collect_all=collect_all, enrich=enrich, role_services=role_services,
                  post_webhook=lambda url, payload: calls.webhooks.append((url, payload)))
    settings = Settings(engine_api_secret=SECRET, companies_house_api_key="ch")
    return TestClient(create_app(settings, store=store, hooks=hooks))


# ---------------------------------------------------------------------------
# fixtures in the database
# ---------------------------------------------------------------------------
def seed(store):
    store.q("insert into searches (id, label, keywords, location) values (1, 'Accountancy - Leeds', "
            "'{management accountant}', 'Leeds'), (2, 'Accountancy - York', '{a}', 'York')")
    for i, (emp, title, n) in enumerate([("Harlow Group", "Management Accountant", 4),
                                         ("Kirkstall Engineering", "Credit Controller", 3),
                                         ("Northgate Foods", "Finance Manager", 3)], 1):
        key = f"key{i}"
        store.q("""insert into hot_leads (lead_key, employer_name, employer_norm, title, location_name,
                   posting_count_90d, first_seen_at, last_seen_at, live_now, long_open, score, latest_url, job_ids)
                   values (%s, %s, %s, %s, 'Leeds', %s, '2026-08-12', now(), true, false, %s, %s, '{1}')""",
                (key, emp, emp.lower(), title, n, 2 * (n - 1) + 1, f"https://www.reed.co.uk/jobs/x/{i}"))
        store.q("insert into hot_lead_searches values (%s, 1)", (key,))
        store.q("insert into lead_contacts (employer_norm, company_name, ch_company_type, company_status, verified_company) "
                "values (%s, %s, 'ltd', 'active', true)", (emp.lower(), emp))
    a = store.one("""insert into agencies (name, name_norm, ch_company_type, ch_company_status, ch_match_confidence,
                     ch_checked_at, ads_count_90d) values ('Aire Valley Recruitment', 'aire valley recruitment', 'ltd',
                     'active', 1, now(), 5) returning *""")
    store.q("insert into agency_searches values (%s, 1, 5)", (a["id"],))
    store.q("""insert into contacts (agency_id, full_name, first_name, role, email, email_verified, source) values
               (%s, 'John Smith', 'John', 'Director', 'john@ava.example', true, 'companies_house'),
               (%s, 'Amy Brown', 'Amy', 'Director', 'amy@ava.example', false, 'companies_house')""", (a["id"], a["id"]))
    b = store.one("""insert into agencies (name, name_norm, ch_company_type, ch_checked_at, ch_match_confidence,
                     ch_review_needed) values ('Bobs Recruitment', 'bobs recruitment', 'not_found', now(), 0, true)
                     returning *""")
    store.q("insert into agency_searches values (%s, 1, 2)", (b["id"],))
    store.q("""insert into contacts (agency_id, full_name, first_name, email, email_verified, source) values
               (%s, 'Bob Jones', 'Bob', 'bob@bobs.example', true, 'manual')""", (b["id"],))
    return a, b


# ---------------------------------------------------------------------------
def test_every_endpoint_rejects_requests_without_the_secret(api):
    app = api.app
    checked = 0
    for route in app.routes:
        methods = getattr(route, "methods", set()) - {"HEAD", "OPTIONS"}
        if not methods or route.path in ("/openapi.json",):
            continue
        path = route.path
        for p in ("{search_id}", "{run_id}", "{task_id}", "{agency_id}", "{contact_id}", "{template_id}",
                  "{client_id}", "{delivery_id}", "{brief_id}", "{employer_id}", "{family_id}", "{lead_key}",
                  "{order_id}", "{conversation_id}", "{project_id}", "{row_id}", "{reply_id}"):
            path = path.replace(p, "1")
        for m in methods:
            for headers in ({}, {"X-API-Key": "wrong"}):
                r = api.request(m, path, headers=headers)
                assert r.status_code == 401, (m, path, r.status_code)
                checked += 1
    assert checked > 80


def test_docs_disabled_unless_flag(store):
    off = TestClient(create_app(Settings(engine_api_secret=SECRET), store=store))
    assert off.get("/docs").status_code == 404
    on = TestClient(create_app(Settings(engine_api_secret=SECRET, enable_docs=True), store=store))
    assert on.get("/docs").status_code == 200


def test_health(api):
    r = api.get("/health", headers=H)
    assert r.status_code == 200 and r.json()["db"] is True


def test_run_all_is_fast_and_completes_in_background(api, store, calls):
    store.create_search("A", ["x"], "Leeds", 15, None)
    store.create_search("B", ["y"], "York", 15, None)
    t0 = time.monotonic()
    r = api.post("/run-all", headers=H)
    assert r.status_code == 202 and time.monotonic() - t0 < 2
    ids = r.json()["run_ids"]
    assert len(ids) == 2
    for rid in ids:
        assert api.get(f"/runs/{rid}", headers=H).json()["status"] == "ok"
    assert len(calls.collect_all) == 1


def test_run_one_search(api, store, calls):
    s = store.create_search("A", ["x"], "Leeds", 15, None)
    r = api.post(f"/searches/{s.id}/run", headers=H)
    assert r.status_code == 202
    assert calls.collect_one == [(s.id, r.json()["run_id"])]
    assert api.post("/searches/999/run", headers=H).status_code == 404


def test_searches_crud_and_overview(api):
    r = api.post("/searches", headers=H, json={"label": "M&E - North West", "keywords": ["electrical estimator", " "],
                                               "location": "Manchester", "radius_miles": 30})
    assert r.status_code == 201 and r.json()["keywords"] == ["electrical estimator"]
    sid = r.json()["id"]
    assert api.patch(f"/searches/{sid}", headers=H, json={"active": False}).json()["active"] is False
    ov = api.get("/searches", headers=H).json()
    assert ov[0]["hot_leads"] == 0 and ov[0]["owner"] is None


def test_hot_leads_filters_and_status(api, store):
    seed(store)
    rows = api.get("/hot-leads", headers=H, params={"search_id": 1, "min_score": 6}).json()
    assert [r["employer_name"] for r in rows] == ["Harlow Group"]
    assert api.patch("/hot-leads/key1", headers=H, json={"status": "used"}).json()["status"] == "used"
    assert api.patch("/hot-leads/key1", headers=H, json={"status": "bogus"}).status_code == 422
    assert len(api.get("/hot-leads", headers=H, params={"status": "used"}).json()) == 1


def test_daily_leads_never_returns_the_same_lead_twice(api, store):
    a, _ = seed(store)
    c = api.post("/clients", headers=H, json={"agency_id": a["id"], "plan": "pilot", "search_ids": [1],
                                              "sheet_url": "https://docs.google.com/spreadsheets/d/abc",
                                              "delivery_email": "ops@ava.example"}).json()
    assert store.get_agency(a["id"])["stage"] == "pilot"
    active = api.get("/clients/active", headers=H).json()
    assert active[0]["sheet_url"].endswith("/abc") and active[0]["territories"][0]["search_id"] == 1
    assert active[0]["sheet_id"] == "abc"
    peek = api.get(f"/clients/{c['id']}/daily-leads", headers=H).json()
    assert peek["count"] == 3                                    # without mark: nothing recorded
    day1 = api.get(f"/clients/{c['id']}/daily-leads", headers=H, params={"mark": True}).json()
    assert day1["count"] == 3
    first = day1["leads"][0]
    assert first["company"] == "Harlow Group" and first["times_posted"] == 4 and first["first_seen"] == "Open since 12 Aug 2026"
    assert first["suggested_email"].startswith("Hello,")                 # no decision maker yet
    assert "Harlow Group has advertised the Management Accountant role 4 times since August 2026" in first["suggested_email"]
    assert "Accountancy recruitment around Leeds" in first["suggested_email"]
    assert first["suggested_opener"] == first["suggested_email"]
    day2 = api.get(f"/clients/{c['id']}/daily-leads", headers=H, params={"mark": True}).json()
    assert day2["count"] == 0
    store.q("""insert into hot_leads (lead_key, employer_name, employer_norm, title, posting_count_90d, first_seen_at,
               last_seen_at, live_now, long_open, score, job_ids) values ('key9', 'New Co', 'new co', 'Payroll Officer',
               3, now(), now(), true, false, 5, '{9}')""")
    store.q("insert into hot_lead_searches values ('key9', 1)")
    store.q("insert into lead_contacts (employer_norm, verified_company) values ('new co', true)")
    day3 = api.get(f"/clients/{c['id']}/daily-leads", headers=H, params={"mark": True}).json()
    assert [l["company"] for l in day3["leads"]] == ["New Co"]
    assert store.val("select count(*) from deliveries") == 4


def test_territory_exclusivity(api, store):
    a, b = seed(store)
    api.post("/clients", headers=H, json={"agency_id": a["id"], "search_ids": [1]})
    r = api.post("/clients", headers=H, json={"agency_id": b["id"], "search_ids": [1]})
    assert r.status_code == 409 and "Accountancy - Leeds" in r.json()["detail"]
    assert store.val("select count(*) from clients") == 1        # nothing half-created
    ov = {s["id"]: s for s in api.get("/searches", headers=H).json()}
    assert ov[1]["owner"]["agency"] == "Aire Valley Recruitment"


def test_new_client_triggers_make_webhook_and_sheet_patch(api, store, calls):
    a, _ = seed(store)
    api.patch("/settings", headers=H, json={"make_webhooks": {"new_client": "https://hook.make.com/abc"}})
    c = api.post("/clients", headers=H, json={"agency_id": a["id"], "search_ids": [1],
                                              "delivery_email": "ops@ava.example"}).json()
    assert calls.webhooks == [("https://hook.make.com/abc", {"client_id": c["id"], "agency": "Aire Valley Recruitment",
                                                             "delivery_email": "ops@ava.example"})]
    r = api.patch(f"/clients/{c['id']}/sheet", headers=H, json={"sheet_url": "https://docs.google.com/x"})
    assert r.json()["sheet_url"] == "https://docs.google.com/x"
    conv = api.post(f"/clients/{c['id']}/convert", headers=H).json()
    assert conv["plan"] == "done_for_you" and conv["monthly_price_gbp"] == 1250
    assert store.get_agency(a["id"])["stage"] == "paid"


def test_outreach_export_skips_non_compliant_rows_and_reports_why(api, store):
    a, b = seed(store)
    store.q("""insert into contacts (agency_id, full_name, first_name, email, email_verified, source, do_not_contact)
               values (%s, 'Dee Nott', 'Dee', 'dee@ava.example', true, 'manual', true)""", (a["id"],))
    store.q("""insert into contacts (agency_id, full_name, first_name, email, email_verified, source)
               values (%s, 'Sue Pressed', 'Sue', 'sue@ava.example', true, 'manual')""", (a["id"],))
    store.suppress("sue@ava.example")
    r = api.get("/exports/outreach.csv", headers=H, params={"search_id": 1, "stage": "new"})
    assert r.status_code == 200
    rows = list(csv.DictReader(io.StringIO(r.text)))
    assert [x["email"] for x in rows] == ["john@ava.example"]
    row = rows[0]
    assert (row["agency"], row["niche"], row["city"], row["lead1_employer"], row["lead1_count"], row["lead1_since"]) == (
        "Aire Valley Recruitment", "Accountancy", "Leeds", "Harlow Group", "4", "August")
    skipped = json.loads(r.headers["X-Skipped"])
    assert skipped == {"no_verified_email": 1, "do_not_contact": 1, "suppressed": 1, "company_type_not_ltd_llp_plc": 1}
    store.update_agency(a["id"], {"stage": "contacted"})
    r2 = api.get("/exports/outreach.csv", headers=H, params={"stage": "new"})
    assert json.loads(r2.headers["X-Skipped"]).get("not_at_stage") == 1 and r2.headers["X-Exported"] == "0"


def test_export_skips_rows_with_missing_template_variables(api, store):
    a, _ = seed(store)
    store.q("delete from hot_leads where lead_key = 'key3'")    # only 2 leads -> lead3 empty
    r = api.get("/exports/outreach.csv", headers=H, params={"search_id": 1})
    assert json.loads(r.headers["X-Skipped"])["missing_template_variable"] == 1


def test_draft_fills_template_and_warns_on_missing(api, store):
    a, b = seed(store)
    cold = next(t for t in api.get("/templates", headers=H).json() if t["name"] == "Cold email, first touch")
    d = api.post(f"/agencies/{a['id']}/draft", headers=H, params={"template_id": cold["id"]}).json()
    assert d["ok"] and d["subject"] == "4x reposted Accountancy role in Leeds"
    assert "Hi John," in d["body"] and "Harlow Group has posted the same Management Accountant role 4 times since August" in d["body"]
    assert "Roberts, SmartEmploy" in d["body"] and 'Reply "stop"' in d["body"]
    assert "http" not in d["body"]                                # no links in first-touch emails
    store.q("delete from hot_leads where lead_key in ('key2', 'key3')")
    d2 = api.post(f"/agencies/{a['id']}/draft", headers=H, params={"template_id": cold["id"]}).json()
    assert not d2["ok"] and d2["missing"] == ["lead2_employer", "lead2_count", "lead2_role"]
    api.patch(f"/agencies/{a['id']}", headers=H, json={"stage": "do_not_contact"})
    d3 = api.post(f"/agencies/{a['id']}/draft", headers=H, params={"template_id": cold["id"]}).json()
    assert d3["blocked"] == "agency is marked do not contact" and d3["body"] == ""


def test_do_not_contact_is_forever(api, store):
    a, _ = seed(store)
    api.patch(f"/agencies/{a['id']}", headers=H, json={"stage": "do_not_contact"})
    assert all(c["do_not_contact"] for c in store.contacts(a["id"]))
    assert store.is_suppressed("john@ava.example")
    api.patch(f"/agencies/{a['id']}", headers=H, json={"stage": "new"})      # even if moved back later
    r = api.get("/exports/outreach.csv", headers=H)
    assert r.headers["X-Exported"] == "0"


def test_contacted_requires_how(api, store):
    a, _ = seed(store)
    r = api.patch(f"/agencies/{a['id']}", headers=H, json={"stage": "contacted"})
    assert r.status_code == 409 and r.json()["message"] == "How did you contact them?"
    r = api.patch(f"/agencies/{a['id']}", headers=H, json={"stage": "contacted", "log_activity_type": "linkedin_dm"})
    assert r.status_code == 200 and r.json()["stage"] == "contacted"
    assert store.activities(a["id"])[0]["type"] == "linkedin_dm"


def test_activities_count_toward_today(api, store):
    a, _ = seed(store)
    for t in ("email", "call", "linkedin_dm", "linkedin_connect", "note", "meeting"):
        assert api.post("/activities", headers=H, json={"agency_id": a["id"], "type": t}).status_code == 201
    assert api.post("/activities", headers=H, json={"agency_id": a["id"], "type": "fax"}).status_code == 422
    t = api.get("/today", headers=H).json()
    assert t["scorecard"]["today"] == 4 and t["target"] == 100 and t["scorecard"]["meetings_week"] == 1
    assert [x["name"] for x in t["do_next"]] == ["Aire Valley Recruitment"]


def test_forget_deletes_contact_and_keeps_only_hash(api, store):
    a, _ = seed(store)
    c = store.contacts(a["id"])[0]
    api.post("/activities", headers=H, json={"agency_id": a["id"], "type": "email", "contact_id": c["id"]})
    assert api.post(f"/contacts/{c['id']}/forget", headers=H).json() == {"forgotten": True}
    assert store.contact(c["id"]) is None
    assert store.is_suppressed(c["email"])
    assert not store.q("select 1 from suppression_list where email_hash like '%%@%%'")
    assert store.activities(a["id"])[0]["contact_id"] is None
    # the same person can't be re-added with that email
    r = api.post(f"/agencies/{a['id']}/contacts", headers=H, json={"full_name": "John Smith", "email": c["email"]})
    assert r.status_code == 201 and r.json()["email"] is None


def test_digest(api, store):
    a, _ = seed(store)
    store.update_agency(a["id"], {"next_follow_up": dt.date(2026, 1, 1)})
    d = api.get("/digest/today", headers=H).json()
    assert d["yesterday"]["target"] == 100 and len(d["new_hot_leads"]) == 3
    assert "Follow-ups due: 1" in d["text"] and "Aire Valley Recruitment" in d["text"]


def test_enrich_returns_task(api, store, calls):
    a, _ = seed(store)
    r = api.post(f"/agencies/{a['id']}/enrich", headers=H)
    assert r.status_code == 202
    t = api.get(f"/tasks/{r.json()['task_id']}", headers=H).json()
    assert t["status"] == "ok" and calls.enrich == [a["id"]]


def test_agency_detail_bundle_and_global_search(api, store):
    a, b = seed(store)
    d = api.get(f"/agencies/{a['id']}", headers=H).json()
    assert d["agency"]["email_allowed"] and d["agency"]["compliance"] == "ltd"
    top = [h["employer_name"] for h in d["top_hot_leads"]]
    assert top[0] == "Harlow Group" and set(top[1:]) == {"Kirkstall Engineering", "Northgate Foods"}  # tie on score
    assert len(d["contacts"]) == 2
    bob = api.get(f"/agencies/{b['id']}", headers=H).json()["agency"]
    assert not bob["email_allowed"] and "not on Companies House" in bob["compliance"]
    g = api.get("/search", headers=H, params={"q": "harl"}).json()
    assert g["hot_leads"][0]["employer_name"] == "Harlow Group"
    assert api.get("/search", headers=H, params={"q": "aire"}).json()["agencies"][0]["name"] == "Aire Valley Recruitment"


def test_templates_crud_and_preview(api, store):
    a, _ = seed(store)
    t = api.post("/templates", headers=H, json={"name": "T", "channel": "email", "subject": "Hi {first_name}",
                                                "body": "{lead1_employer} x{lead1_count}"}).json()
    assert api.post("/templates", headers=H, json={"name": "T", "channel": "sms", "body": "x"}).status_code == 422
    p = api.post("/templates/preview", headers=H, json={"subject": t["subject"], "body": t["body"],
                                                        "agency_id": a["id"]}).json()
    assert p["subject"] == "Hi John" and p["body"] == "Harlow Group x4"
    assert api.delete(f"/templates/{t['id']}", headers=H).status_code == 204


def test_settings(api):
    s = api.patch("/settings", headers=H, json={"daily_outreach_target": 120, "repost_threshold": 2}).json()
    assert s["daily_outreach_target"] == 120 and s["repost_threshold"] == 2
    assert api.patch("/settings", headers=H, json={"hack": 1}).status_code == 422
    got = api.get("/settings", headers=H).json()
    assert got["configured"]["companies house"] is True and got["configured"]["theirstack"] is False
    assert {x["name"]: x["key_set"] for x in got["sources"]}["reed"] is False


def test_role_match_endpoints(api, store, calls):
    a, _ = seed(store)
    r = api.post("/briefs", headers=H, json={"agency_id": a["id"], "role": "Electrical Estimator",
                                             "location": "Warrington", "radius_miles": 30})
    assert r.status_code == 201 and r.json()["label"] == "Electrical Estimator - Warrington"
    assert api.post("/briefs", headers=H, json={"agency_id": a["id"], "role": "Plumber", "location": "X"}).status_code == 422
    bid = r.json()["id"]
    emp = api.post("/employers", headers=H, json={"name": "Acme M&E", "careers_url": "https://acme.example/careers"}).json()
    assert emp["status"] == "active"
    job = store.upsert_job({"employer_id": emp["id"], "source": "careers_page", "title": "Electrical Estimator",
                            "title_norm": "electrical estimator", "location_text": None, "url": "https://acme.example/j1"})
    store.upsert_match({"brief_id": bid, "employer_job_id": job["id"], "score": 6, "reasons": ["Exact title match (+4)"],
                        "distance_miles": 4.2, "signal": "Live on their careers page", "open_since_label": "unknown",
                        "location_label": "Warrington (company base)"})
    ms = api.get(f"/agencies/{a['id']}/matches", headers=H).json()
    assert ms[0]["company"] == "Acme M&E" and ms[0]["signal"] == "Live on their careers page"
    assert ms[0]["link"] == "https://acme.example/j1" and ms[0]["location"] == "Warrington (company base)"
    assert api.post("/matches/status", headers=H, json={"ids": [ms[0]["id"]], "status": "sent"}).json() == {"updated": 1}
    csv_text = api.get("/exports/matches.csv", headers=H, params={"agency_id": a["id"]}).text
    rows = list(csv.reader(io.StringIO(csv_text)))
    assert rows[0][0] == "candidate_role" and rows[1][1] == "Acme M&E" and rows[1][4] == "4"
    assert rows[-1][6].startswith("Market demand")
    prev = api.get("/role-families/1/preview", headers=H).json()
    assert prev[0]["title"] == "Electrical Estimator"
    # send to sheet needs a webhook and a sheet
    assert api.post(f"/agencies/{a['id']}/matches/send-to-sheet", headers=H).status_code == 400
    api.patch("/settings", headers=H, json={"make_webhooks": {"matches_to_sheet": "https://hook.make.com/m"}})
    r = api.post(f"/agencies/{a['id']}/matches/send-to-sheet", headers=H, params={"sheet_url": "https://docs/x"})
    assert r.status_code == 202 and calls.webhooks[-1] == ("https://hook.make.com/m", {"agency_id": a["id"],
                                                                                      "sheet_url": "https://docs/x"})


def test_create_agency_by_name(api):
    r = api.post("/agencies", headers=H, json={"name": "MCM Recruitment Solutions"})
    assert r.status_code == 201 and r.json()["name"] == "MCM Recruitment Solutions"
    assert api.post("/agencies", headers=H, json={"name": "MCM Recruitment Solutions"}).json()["id"] == r.json()["id"]
