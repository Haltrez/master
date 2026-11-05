"""Lead orders, Google Sheets (faked over HTTP) and the AI assistant (fake Claude client)."""

import datetime as dt
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from app import orders as O
from app.config import Settings
from app.google_sheets import Sheets, auth_url, exchange_code
from app.main import Hooks, create_app
from app.pg import TerritoryTaken

SECRET = "s"
H = {"X-API-Key": SECRET}


def add_leads(store, n, location="Leeds", title="Software Developer", verified=True, label=None, start=0,
              description="Build and maintain our web platform as part of our in-house team."):
    """n reposted hot leads (each with its newest ad as a listing) tied to the search with this label."""
    sid = store.val("select id from searches where label = %s", (label,))
    for i in range(start, start + n):
        key, emp, job = f"k{i}", f"Company {i}", 70000 + i
        store.q("""insert into listings (job_id, source, posted_by, employer_name, employer_norm, title, title_norm,
                   location_name, location_norm, url, posted_date, first_seen_at, last_seen_at, raw, description)
                   values (%s, 'adzuna', 'employer', %s, %s, %s, %s, %s, %s, %s, current_date - 2, now() - interval '2 days',
                   now(), '{}', %s)""",
                (job, emp, emp.lower(), title, title.lower(), location, location.lower(), f"https://example.test/{i}", description))
        store.q("""insert into hot_leads (lead_key, employer_name, employer_norm, title, location_name, posting_count_90d,
                   first_seen_at, last_seen_at, live_now, long_open, score, latest_url, job_ids, posting_dates, previous_url)
                   values (%s, %s, %s, %s, %s, 3, '2026-09-01', now(), true, false, %s, %s, %s, '{2026-09-01,2026-10-01,2026-11-01}',
                   'https://example.test/old')""",
                (key, emp, emp.lower(), title, location, 10 + i, f"https://example.test/{i}", [job]))
        store.q("insert into hot_lead_searches values (%s, %s)", (key, sid))
        store.q("insert into lead_contacts (employer_norm, company_name, ch_company_type, verified_company) "
                "values (%s, %s, 'ltd', %s)", (emp.lower(), emp, verified))


class FakeSheets:
    def __init__(self):
        self.created, self.rows, self.shared = [], [], []

    def create(self, title, headers):
        self.created.append(title)
        return {"sheet_id": "SID1", "sheet_url": "https://docs.google.com/spreadsheets/d/SID1/edit"}

    def append(self, sid, rows):
        self.rows.extend(rows)

    def share(self, sid, email):
        self.shared.append(email)


def make(store, **kw):
    return O.create_order(store, **{"agency_name": "Acme Recruitment", "industry": "IT & Tech", "location": "Leeds",
                                   "leads_wanted": 3, **kw})


# ---------------------------------------------------------------------------
def test_create_order_sets_up_agency_client_and_exclusive_search(store):
    o = make(store, client_email="jo@acme.test")
    assert o["agency_name"] == "Acme Recruitment" and o["status"] == "collecting"
    s = store.search_row(o["search_id"])
    assert s["label"] == "IT & Tech - Leeds" and "software developer" in s["keywords"]
    c = store.client(o["client_id"])
    assert c["territories"][0]["search_id"] == o["search_id"] and c["delivery_email"] == "jo@acme.test"
    # same agency again: same client + territory; another agency: refused
    again = make(store)
    assert again["client_id"] == o["client_id"] and again["search_id"] == o["search_id"]
    with pytest.raises(TerritoryTaken):
        make(store, agency_name="Other Agency")
    # nothing half-made for the refused one
    assert store.agency_by_name("Other Agency") is None


def test_custom_keywords_and_validation(store):
    o = make(store, industry="Dental", keywords=["Dental Nurse", "dental nurse", "practice manager"])
    assert store.search_row(o["search_id"])["keywords"] == ["dental nurse", "practice manager"]
    assert O.keywords_for("Something new") == ["something new"]
    with pytest.raises(ValueError):
        make(store, leads_wanted=0)
    with pytest.raises(ValueError):
        make(store, frequency="daily")


def test_fill_sends_only_verified_up_to_the_number_and_never_twice(store):
    o = make(store, client_email="jo@acme.test")
    add_leads(store, 4, label="IT & Tech - Leeds")
    add_leads(store, 2, label="IT & Tech - Leeds", verified=False, start=10)
    sheets, collected = FakeSheets(), []
    r = O.fill_order(store, Settings(), o["id"], sheets=sheets, collect=lambda s, st, sid: collected.append(sid))
    assert collected == [o["search_id"]]
    assert r["sent"] == 3 and r["status"] == "done"
    assert sheets.shared == ["jo@acme.test"] and len(sheets.created) == 1
    assert [row[1] for row in sheets.rows] == ["Company 3", "Company 2", "Company 1"]   # best score first
    assert sheets.rows[0][-1] == "new" and len(sheets.rows[0]) == len(O.SHEET_HEADERS) == 16
    got = O.get_order(store, o["id"])
    assert got["delivered_total"] == 3 and got["sheet_url"].endswith("/edit")
    # raising the number sends only the rest; unverified companies never go out
    O.update_order(store, o["id"], {"leads_wanted": 10, "status": "waiting"})
    r = O.fill_order(store, Settings(), o["id"], sheets=sheets)
    assert r["sent"] == 1 and r["status"] == "waiting" and "4 of 10" in r["note"]
    assert len(sheets.created) == 1


def test_without_google_leads_wait_in_csv_and_backfill_the_sheet_later(store):
    o = make(store, leads_wanted=2)
    add_leads(store, 2, label="IT & Tech - Leeds")
    r = O.fill_order(store, Settings(), o["id"])
    assert r["status"] == "done" and "CSV" in r["note"]
    assert len(O.order_lead_rows(store, o["id"])) == 2
    O.update_order(store, o["id"], {"leads_wanted": 3, "status": "waiting"})
    add_leads(store, 1, label="IT & Tech - Leeds", start=5)
    sheets = FakeSheets()
    O.fill_order(store, Settings(), o["id"], sheets=sheets)
    assert len(sheets.rows) == 3   # the 2 sent earlier + the new one


def test_sheet_failure_keeps_leads_for_the_retry(store):
    o = make(store)
    add_leads(store, 2, label="IT & Tech - Leeds")

    class Broken(FakeSheets):
        def append(self, sid, rows):
            raise RuntimeError("quota")
    r = O.fill_order(store, Settings(), o["id"], sheets=Broken())
    assert r["status"] == "error" and O.get_order(store, o["id"])["delivered_total"] == 0
    assert O.fill_order(store, Settings(), o["id"], sheets=FakeSheets())["sent"] == 2


def test_weekly_orders_send_a_new_batch_each_week(store):
    o = make(store, leads_wanted=2, frequency="weekly")
    add_leads(store, 5, label="IT & Tech - Leeds")
    assert O.fill_order(store, Settings(), o["id"])["sent"] == 2
    assert O.fill_order(store, Settings(), o["id"])["sent"] == 0
    later = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=7, hours=1)
    r = O.fill_order(store, Settings(), o["id"], now=later)
    assert r["sent"] == 2 and r["status"] == "waiting"


def test_paused_orders_are_left_alone(store):
    o = make(store)
    add_leads(store, 2, label="IT & Tech - Leeds")
    O.update_order(store, o["id"], {"status": "paused"})
    assert O.top_up_all(store, Settings()) == []
    with pytest.raises(ValueError):
        O.update_order(store, o["id"], {"status": "done"})


# ---------------------------------------------------------------------------
# Google over HTTP
# ---------------------------------------------------------------------------
def test_google_oauth_and_sheet_calls(store):
    seen = []

    def handler(req: httpx.Request):
        seen.append((req.method, req.url.path, req.content.decode(), str(req.url)))
        if req.url.path == "/token":
            body = dict(x.split("=") for x in req.content.decode().split("&"))
            if body["grant_type"] == "authorization_code":
                import base64
                payload = base64.urlsafe_b64encode(json.dumps({"email": "me@x.test"}).encode()).decode().rstrip("=")
                return httpx.Response(200, json={"refresh_token": "R", "access_token": "A", "id_token": f"h.{payload}.s"})
            return httpx.Response(200, json={"access_token": "A2", "expires_in": 3600})
        if req.url.path == "/v4/spreadsheets":
            return httpx.Response(200, json={"spreadsheetId": "S9", "spreadsheetUrl": "https://docs.google.com/x",
                                             "sheets": [{"properties": {"sheetId": 0}}]})
        return httpx.Response(200, json={})
    t = httpx.MockTransport(handler)
    assert "drive.file" in auth_url("cid", "https://crm/cb", "st")
    assert exchange_code(store, "cid", "sec", "CODE", "https://crm/cb", transport=t) == {"connected": True, "email": "me@x.test"}
    sh = Sheets.from_store(store, Settings(google_client_id="cid", google_client_secret="sec"), transport=t)
    made = sh.create("T", O.SHEET_HEADERS)
    sh.append(made["sheet_id"], [["=HYPERLINK(1)", None]])
    sh.share(made["sheet_id"], "jo@acme.test")
    paths = [x[1] for x in seen]
    assert paths.count("/token") == 2   # the access token is reused
    assert "/v4/spreadsheets/S9:batchUpdate" in paths and "/drive/v3/files/S9/permissions" in paths
    append = [x for x in seen if x[1].endswith(":append")][-1]
    assert '["=HYPERLINK(1)",""]' in append[2] and "valueInputOption=RAW" in append[3]


# ---------------------------------------------------------------------------
# API + assistant
# ---------------------------------------------------------------------------
class FakeClaude:
    """Plays back scripted responses and records each request."""

    def __init__(self, script):
        self.script, self.requests = list(script), []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.requests.append(json.loads(json.dumps(kw["messages"], default=str)))
        assert kw["fallbacks"] == "default" and kw["output_config"] == {"effort": "medium"}
        assert all(t["strict"] and t["input_schema"]["additionalProperties"] is False for t in kw["tools"])
        return self.script.pop(0)


class Block(SimpleNamespace):
    def to_dict(self):
        return {k: v for k, v in vars(self).items()}


def resp(stop, *blocks):
    return SimpleNamespace(stop_reason=stop, content=list(blocks))


@pytest.fixture
def api_with(store):
    def build(claude=None, sheets=None):
        runs = []

        def collect_one(s, st, sid, rid):
            runs.append(sid)
            s.finish_run(rid, status="ok", api_calls=1, listings_seen=0, new_listings=0, error=None)
        hooks = Hooks(collect_one=collect_one, collect_all=lambda *a: None, enrich=lambda *a: {},
                      role_services=lambda *a: None, post_webhook=lambda *a: None,
                      make_sheets=lambda s, st: sheets, make_claude=lambda st: claude)
        c = TestClient(create_app(Settings(engine_api_secret=SECRET), store=store, hooks=hooks))
        c.runs = runs
        return c
    return build


def test_orders_api(store, api_with):
    sheets = FakeSheets()
    c = api_with(sheets=sheets)
    assert any(i["name"] == "IT & Tech" for i in c.get("/industries", headers=H).json())
    # leads exist before the order's collection finishes (seeded under the label the order will use)
    store.q("insert into searches (label, keywords, location) values ('IT & Tech - Leeds', '{x}', 'Leeds')")
    add_leads(store, 3, label="IT & Tech - Leeds")
    r = c.post("/orders", headers=H, json={"agency_name": "Acme", "industry": "IT & Tech", "location": "Leeds",
                                           "leads_wanted": 2, "client_email": "jo@acme.test"})
    assert r.status_code == 201, r.text
    oid = r.json()["id"]
    o = c.get(f"/orders/{oid}", headers=H).json()
    assert c.runs and o["status"] == "done" and o["delivered_total"] == 2 and o["ready"] == 1
    assert o["sheet_url"] and sheets.shared == ["jo@acme.test"]
    assert c.post(f"/orders/{oid}/send-more", headers=H).status_code == 409
    csv_text = c.get(f"/orders/{oid}/leads.csv", headers=H).text
    import csv, io
    rows = list(csv.reader(io.StringIO(csv_text.lstrip("\ufeff"))))
    assert rows[0] == O.SHEET_HEADERS and len(rows) == 3 and rows[1][1] == "Company 2"
    taken = c.post("/orders", headers=H, json={"agency_name": "Rival", "industry": "IT & Tech", "location": "Leeds"})
    assert taken.status_code == 409 and "Acme" in taken.json()["detail"]
    assert c.post("/orders", headers=H, json={"agency_name": "X", "industry": "Y", "location": "Z",
                                              "leads_wanted": 0}).status_code == 422
    assert c.patch(f"/orders/{oid}", headers=H, json={"status": "paused"}).json()["status"] == "paused"
    assert c.post(f"/orders/{oid}/send-more", headers=H).status_code == 409
    st = c.get("/settings", headers=H).json()
    assert st["google"]["connected"] is False and "google_oauth" not in st["settings"]


def test_google_token_never_leaks_through_settings(store, api_with):
    store.set_setting("google_oauth", {"refresh_token": "SECRET-R", "email": "me@x"})
    c = api_with()
    body = c.get("/settings", headers=H).text
    assert "SECRET-R" not in body and '"connected":true' in body.replace(" ", "")


def test_assistant_creates_an_order_with_tools(store, api_with):
    thinking = Block(type="thinking", thinking="", signature="sig")
    claude = FakeClaude([
        resp("tool_use", thinking, Block(type="text", text="On it."),
             Block(type="tool_use", id="tu1", name="create_lead_order",
                   input={"agency_name": "Acme", "industry": "IT & Tech", "location": "Leeds", "leads_wanted": 5})),
        resp("end_turn", Block(type="text", text="Order 1 is collecting.")),
        resp("tool_use", Block(type="tool_use", id="tu2", name="list_orders", input={})),
        resp("end_turn", Block(type="text", text="You have 1 order.")),
    ])
    c = api_with(claude=claude)
    r = c.post("/assistant/chat", headers=H, json={"message": "5 IT leads in Leeds for Acme"}).json()
    assert r["reply"] == "Order 1 is collecting." and r["actions"] == ["create_lead_order"]
    assert O.list_orders(store)[0]["agency_name"] == "Acme" and c.runs
    # 2nd request carried the assistant turn unchanged (thinking first) + the tool result
    second = claude.requests[1]
    assert second[1]["content"][0] == {"type": "thinking", "thinking": "", "signature": "sig"}
    assert second[2]["content"][0]["tool_use_id"] == "tu1"
    r2 = c.post("/assistant/chat", headers=H, json={"message": "what orders?", "conversation_id": r["conversation_id"]}).json()
    assert r2["reply"] == "You have 1 order." and len(claude.requests[2]) == 5   # history reloaded
    hist = c.get(f"/assistant/conversations/{r['conversation_id']}", headers=H).json()["messages"]
    assert [m["role"] for m in hist] == ["user", "assistant", "assistant", "user", "assistant"]


def test_assistant_tool_errors_go_back_to_the_model(store, api_with):
    claude = FakeClaude([
        resp("tool_use", Block(type="tool_use", id="t", name="create_lead_order",
                               input={"agency_name": "A", "industry": "B", "location": "C", "leads_wanted": 0})),
        resp("end_turn", Block(type="text", text="That number is too small.")),
    ])
    r = api_with(claude=claude).post("/assistant/chat", headers=H, json={"message": "0 leads"}).json()
    assert r["actions"] == [] and claude.requests[1][-1]["content"][0]["is_error"] is True


def test_assistant_without_key_says_so(store):
    c = TestClient(create_app(Settings(engine_api_secret=SECRET), store=store))
    r = c.post("/assistant/chat", headers=H, json={"message": "hi"})
    assert r.status_code == 400 and "ANTHROPIC_API_KEY" in r.json()["detail"]


class FakeHaiku:
    """Passes ads whose title contains 'sales' unless the text says 'our client'; records each call."""

    def __init__(self):
        self.calls = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kw):
        import re
        self.calls.append(kw)
        assert kw["model"] == "claude-haiku-5-5" and kw["output_config"]["format"]["type"] == "json_schema"
        text = kw["messages"][0]["content"]
        verdicts = []
        for m in re.finditer(r'<ad id="(\d+)">\nCompany: [^\n]*\nTitle: ([^\n]*)\n(.*?)</ad>', text, re.S):
            fit = "sales" in m.group(2).lower()
            direct = "our client" not in m.group(3).lower()
            verdicts.append({"id": int(m.group(1)), "industry_fit": fit, "direct_employer": direct,
                             "real_vacancy": True, "agency_would_fill": True, "not_recruitment_firm": True, "role": m.group(2).title(),
                             "reason": "sales role" if fit else "not sales"})
        return SimpleNamespace(stop_reason="end_turn",
                               content=[SimpleNamespace(type="text", text=json.dumps({"verdicts": verdicts}))])


def test_only_leads_that_fit_the_industry_are_sent_and_verdicts_are_cached(store):
    o = make(store, industry="Sales & Marketing", leads_wanted=10)
    label = "Sales & Marketing - Leeds"
    add_leads(store, 2, label=label, title="Field Sales Executive")
    add_leads(store, 3, label=label, title="Retail Optical Assistant", start=10)
    haiku = FakeHaiku()
    add_leads(store, 1, label=label, title="Sales Executive", start=30, description="Our client, a leading firm...")
    r = O.fill_order(store, Settings(), o["id"], claude=haiku)
    assert r["sent"] == 2 and len(haiku.calls) == 1   # 6 ads in one batch; the agency ad ("our client") is out
    rows = O.order_lead_rows(store, o["id"])
    assert rows[0]["Times posted"] == 3 and "Advertised 3 separate times" in rows[0]["Why it's a lead"]
    assert rows[0]["Role"] == "Field Sales Executive" and rows[0]["Previous ad"] == "https://example.test/old"
    add_leads(store, 1, label=label, title="Retail Optical Assistant", start=20)
    O.fill_order(store, Settings(), o["id"], claude=haiku)
    assert len(haiku.calls) == 2 and haiku.calls[1]["messages"][0]["content"].count("<ad id=") == 1   # only the new ad


def test_without_claude_the_keyword_check_keeps_garbage_out(store):
    o = make(store, industry="Sales & Marketing", leads_wanted=10)
    add_leads(store, 1, label="Sales & Marketing - Leeds", title="Senior Sales Executive")
    add_leads(store, 1, label="Sales & Marketing - Leeds", title="Store Manager", start=5)
    assert O.fill_order(store, Settings(), o["id"])["sent"] == 1


def test_delete_order_frees_the_territory(store):
    o = make(store)
    add_leads(store, 2, label="IT & Tech - Leeds")
    O.fill_order(store, Settings(), o["id"])
    assert O.delete_order(store, o["id"]) and O.get_order(store, o["id"]) is None
    assert store.val("select count(*) from deliveries") == 0 and store.val("select count(*) from hot_leads") == 2
    other = make(store, agency_name="Other Agency")   # no longer taken
    assert other["agency_name"] == "Other Agency"
    assert not O.delete_order(store, 999)


def test_delete_keeps_the_client_while_it_has_other_orders(store):
    a, b = make(store), make(store, leads_wanted=5)
    O.delete_order(store, a["id"])
    assert O.get_order(store, b["id"])["client_id"] == b["client_id"]
    import pytest as _p
    with _p.raises(TerritoryTaken):
        make(store, agency_name="Other Agency")


def test_territories_collected_before_a_sale_and_reused_by_the_order(store):
    t = O.watch_territory(store, "IT & Tech", "York")
    assert t["label"] == "IT & Tech - York" and t["owner"] is None
    assert [x["label"] for x in O.territories(store)] == ["IT & Tech - York"]
    o = O.create_order(store, agency_name="Acme", industry="IT & Tech", location="York", leads_wanted=5)
    assert o["search_id"] == t["search_id"] and O.territory(store, t["search_id"])["owner"] == "Acme"


def test_territories_api(store, api_with):
    c = api_with()
    r = c.post("/territories", headers=H, json={"industry": "Legal", "location": "Bath"})
    assert r.status_code == 201, r.text
    assert c.runs and [t["label"] for t in c.get("/territories", headers=H).json()] == ["Legal - Bath"]


def test_sent_leads_and_agencies_overview(store, api_with):
    o = make(store, client_email="jo@acme.test")
    add_leads(store, 2, label="IT & Tech - Leeds")
    O.fill_order(store, Settings(), o["id"])
    c = api_with()
    leads = c.get(f"/leads?order_id={o['id']}", headers=H).json()
    assert len(leads) == 2 and leads[0]["row"]["Company"].startswith("Company") and leads[0]["agency_name"] == "Acme Recruitment"
    c.patch(f"/deliveries/{leads[0]['id']}", headers=H, json={"outcome": "meeting"})
    ov = c.get("/agencies-overview", headers=H).json()
    assert ov["clients"][0]["name"] == "Acme Recruitment" and ov["clients"][0]["meetings"] == 1
    assert ov["clients"][0]["orders"][0]["sent"] == 2 and ov["clients"][0]["email"] == "jo@acme.test"
    assert c.get("/health", headers=H).json()["version"]
