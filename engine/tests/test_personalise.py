"""Personalising the leads already in the Smartlead campaigns (fake Smartlead, fake Claude, no network)."""

import json
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app import outreach as OR
from app import personalise as PZ
from app.config import Settings
from app.main import Hooks, create_app

H = {"X-API-Key": "s"}
SEQ = [{"seq_number": 1, "subject": "{{lead1_count}}x reposted {{niche}} role in {{city}}",
        "email_body": "<p>Hi {{first_name}},</p><p>Saw {{company_name}} places {{niche}} roles around {{city}}.</p>"
                      "<p>{{lead1_employer}} has posted the same {{lead1_role}} role {{lead1_count}} times since {{lead1_since}}. "
                      "{{lead2_employer}} is on {{lead2_count}} posts for {{lead2_role}}.</p>"},
       {"seq_number": 2, "subject": "", "email_body": "Hi {{first_name}}, one more: {{lead3_employer}} has posted {{lead3_role}} {{lead3_count}} times."}]


def lead(i, company, location, title="Founder", status="STARTED", website=None, first="Jo"):
    return {"campaign_lead_map_id": 100 + i, "status": status,
            "lead": {"id": i, "email": f"p{i}@{(website or 'x' + str(i) + '.co.uk')}", "first_name": first,
                     "company_name": company, "website": website or f"x{i}.co.uk", "location": location,
                     "custom_fields": {"title": title, "sector": "Staffing and Recruiting"}}}


class FakeSL:
    def __init__(self, rows):
        self.rows, self.updates = rows, []

    def campaign_leads(self, cid):
        return self.rows.get(cid, [])

    def update_lead(self, cid, lead_id, email, custom_fields, **std):
        self.updates.append((cid, lead_id, email, custom_fields, std))
        return {"ok": True}

    def sequences(self, cid):
        return SEQ


class FakeClaude:
    """Answers from the homepage text: 'IT recruitment' -> IT agency; 'software' -> not an agency."""
    def __init__(self):
        self.calls = 0
        self.messages = self

    def create(self, **kw):
        self.calls += 1
        text = kw["messages"][0]["content"]
        if "software product" in text:
            out = {"is_agency": False, "niche": "none", "why": "software company"}
        else:
            out = {"is_agency": True, "niche": "IT & Tech", "why": "IT recruitment agency"}
        return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=json.dumps(out))])


class FakeGeo:
    COORDS = {"london": (51.5, -0.12), "leeds": (53.8, -1.55), "wetherby": (53.93, -1.39), "manchester": (53.48, -2.24),
              "truro": (50.26, -5.05)}

    def geocode(self, text):
        c = self.COORDS.get(text.split(",")[0].strip().lower())
        return SimpleNamespace(lat=c[0], lng=c[1]) if c else None


def pages(domain):
    return "We build a software product" if domain.startswith("soft") else "Specialist IT recruitment agency"


def seed_hot_leads(store, label, n, start=0):
    sid = store.val("select id from searches where label = %s", (label,))
    for i in range(start, start + n):
        key, emp = f"{label}-{i}", f"Employer {label} {i} Ltd"
        store.q("""insert into hot_leads (lead_key, employer_name, employer_norm, title, posting_count_90d, first_seen_at,
                   last_seen_at, live_now, long_open, score, job_ids) values (%s, %s, %s, 'Software Engineer', %s, '2026-08-03',
                   now(), true, false, %s, '{}')""", (key, emp, emp.lower(), 2 + i % 4, 100 - i))
        store.q("insert into hot_lead_searches values (%s, %s)", (key, sid))
        store.q("insert into lead_contacts (employer_norm, company_name, verified_company) values (%s, %s, true)", (emp.lower(), emp))


def setup(store):
    store.set_setting(OR.S_LEADERS, 1)
    store.set_setting(OR.S_SALES, 2)
    return FakeSL({1: [lead(1, "Bright IT Recruitment Ltd", "London, England, United Kingdom"),
                       lead(2, "Code People Limited", "London, England, United Kingdom"),
                       lead(3, "Wetherby Tech Talent", "Wetherby, United Kingdom"),
                       lead(4, "SoftCo", "Leeds, United Kingdom", website="softco.io"),
                       lead(5, "Nowhere Recruit", "United Kingdom"),
                       lead(6, "Far Away IT", "Truro, United Kingdom")],
                   2: [lead(7, "Bright IT Recruitment Ltd", "London, England, United Kingdom", title="Recruitment Consultant")]})


def test_each_agency_gets_niche_city_and_three_hot_leads_written_to_smartlead(store):
    sl, claude = setup(store), FakeClaude()
    first = PZ.run(store, Settings(), sl, client=claude, geocoder=FakeGeo(), fetch=pages)
    # territories are watched for every niche + city the agencies need; nothing to send yet
    assert set(first["territories"]) == {"IT & Tech - London", "IT & Tech - Leeds", "IT & Tech - Truro"}
    assert first["filled"] == 0 and not sl.updates
    seed_hot_leads(store, "IT & Tech - London", 9)
    seed_hot_leads(store, "IT & Tech - Leeds", 3)
    res = PZ.run(store, Settings(), sl, client=claude, geocoder=FakeGeo(), fetch=pages)
    assert res["filled"] == 4                                   # 2 London founders, Wetherby (-> Leeds), London sales person
    up = {u[1]: u for u in sl.updates}
    f = up[1][3]
    assert (f["niche"], f["city"]) == ("IT", "London") and up[1][4]["company_name"] == "Bright IT Recruitment"
    assert all(f[k] for k in PZ.FIELDS if k != "company_name") and int(f["lead1_count"]) >= int(f["lead2_count"])
    assert f["lead1_since"] == "August" and not f["lead1_employer"].endswith("Ltd")
    assert up[3][3]["city"] == "Leeds"                          # Wetherby is 12 miles from Leeds
    london = [set(up[i][3][f"lead{j}_employer"] for j in (1, 2, 3)) for i in (1, 2, 7)]
    assert not (london[0] & london[1]) and not (london[1] & london[2])   # neighbours get different companies
    held = {r["lead_id"]: r["reason"] for r in PZ.leads(store, "held")}
    assert "not a recruitment agency" in held[4] and "no town" in held[5] and "only 0 hot leads" in held[6]
    assert claude.calls == 7                                    # one website check per agency, cached after
    # the preview reads exactly as the email will
    p = PZ.preview(store, sl, 1, 1)["steps"]
    assert p[0]["subject"].endswith("reposted IT role in London") and "{{" not in p[0]["body"] + p[1]["body"]
    assert "Saw Bright IT Recruitment places IT roles around London." in p[0]["body"]


def test_morning_refresh_keeps_leads_already_in_the_sequence(store):
    sl, claude = setup(store), FakeClaude()
    PZ.run(store, Settings(), sl, client=claude, geocoder=FakeGeo(), fetch=pages)
    seed_hot_leads(store, "IT & Tech - London", 9)
    PZ.run(store, Settings(), sl, client=claude, geocoder=FakeGeo(), fetch=pages)
    sl.updates.clear()
    again = PZ.run(store, Settings(), sl, client=claude, geocoder=FakeGeo(), fetch=pages)
    assert again["filled"] == 0 and again["unchanged"] == 3 and not sl.updates     # nothing changed: no writes
    sl.rows[1][0]["status"] = "INPROGRESS"                                      # lead 1 got its first email
    seed_hot_leads(store, "IT & Tech - London", 6, start=9)
    store.q("update hot_leads set score = 500 where lead_key like 'IT & Tech - London-1_'")   # newer, better leads
    res = PZ.run(store, Settings(), sl, client=claude, geocoder=FakeGeo(), fetch=pages)
    assert res["kept_in_sequence"] == 1 and 1 not in {u[1] for u in sl.updates} and {2, 7} <= {u[1] for u in sl.updates}


def test_api_summary_held_csv_and_run(store):
    sl = setup(store)
    claude = FakeClaude()
    hooks = Hooks(collect_one=lambda *a: None, collect_all=lambda *a: None, enrich=lambda *a: {}, role_services=lambda *a: None,
                  post_webhook=lambda *a: None, make_smartlead=lambda st: sl,
                  make_tools=lambda s, st: SimpleNamespace(claude=claude, geocoder=FakeGeo(), ch=None))
    api = TestClient(create_app(Settings(engine_api_secret="s", smartlead_api_key="k"), store=store, hooks=hooks))
    PZ_fetch, PZ._homepage = PZ._homepage, pages
    try:
        assert api.post("/outreach/personalise/run", headers=H).status_code == 202
    finally:
        PZ._homepage = PZ_fetch
    s = api.get("/outreach/personalise", headers=H).json()
    assert s["by_campaign"]["leaders"]["held"] == 6 and s["last_run"]["synced"] == 7
    csv_text = api.get("/outreach/personalise/held.csv", headers=H).text
    assert csv_text.startswith("email,campaign,company,reason") and "p5@x5.co.uk" in csv_text
    assert api.get("/outreach/personalise", headers={"X-API-Key": "wrong"}).status_code == 401
    assert s["running"] is None
    stale = store.create_task("personalise")         # 'running' from before an engine restart: marked stopped, new run goes
    r = api.post("/outreach/personalise/run", headers=H).json()
    assert r["task_id"] != stale and store.get_task(stale)["status"] == "error"
