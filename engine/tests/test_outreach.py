"""Outreach through Smartlead, against an in-memory fake Smartlead (the real API is never called in tests)."""

import datetime as dt
import logging

import pytest
from fastapi.testclient import TestClient

from app import outreach as OR
from app.config import Settings
from app.main import Hooks, create_app

KEY = "sl-secret-key-123"
H = {"X-API-Key": "s"}
TODAY = dt.date(2026, 11, 6)
SEQ = [{"seq_number": 1, "subject": "{{lead1_employer}} keeps reposting in {{city}}",
        "email_body": "<p>Hi {{first_name}},</p><p>{{lead1_employer}} has posted {{lead1_role}} {{lead1_count}} times since "
                      "{{lead1_since}}, and {{lead2_employer}} the same with {{lead2_role}}. {{niche}} in {{city}}? "
                      "{{sender_name}}</p>"}]


SEQ_ONE = [{"seq_number": 1, "subject": "{{lead1_employer}} in {{city}}",
            "email_body": "<p>Hi {{first_name}}, {{lead1_employer}} posted {{lead1_role}} {{lead1_count}} times.</p>"}]


class FakeSmartlead:
    def __init__(self):
        self.calls, self.leads, self.status, self.unsub, self.blocked, self.categories = [], {}, {}, set(), [], {}
        self.next_id = 1000
        self.replies = []
        self.stats_rows = {}

    def _c(self, *a):
        self.calls.append(a)

    def campaigns(self):
        return [{"id": 11, "name": "Founders", "status": self.status.get(11, "ACTIVE"), "max_leads_per_day": 50},
                {"id": 22, "name": "Sales", "status": self.status.get(22, "ACTIVE"), "max_leads_per_day": 20}]

    def set_status(self, cid, status):
        self._c("status", cid, status)
        self.status[cid] = "PAUSED" if status == "PAUSED" else "ACTIVE"
        return {"ok": True}

    def analytics_by_date(self, cid, start, end, tz="Europe/London"):
        return {"sent_count": "100", "reply_count": "4", "non_ooo_reply_count": "3", "bounce_count": "3", "unsubscribed_count": "1"}

    def campaign_email_accounts(self, cid):
        return [{"id": 1}, {"id": 2}]

    def sequences(self, cid):
        return SEQ if cid == 11 else SEQ_ONE

    def statistics(self, cid, limit=1000, offset=0, since=None):
        return self.stats_rows.get(cid, [])

    def add_lead(self, cid, lead, allow_other_campaigns=False):
        self._c("add", cid, lead["email"])
        if lead["email"].startswith("skip"):
            return {"added_count": 0, "skipped_count": 1, "skipped_leads": ["global block list"]}
        self.next_id += 1
        self.leads[lead["email"]] = {"id": self.next_id, "campaign": cid, "lead": lead}
        return {"success": True, "added_count": 1, "skipped_count": 0, "lead_ids": [self.next_id]}

    def lead_by_email(self, email):
        x = self.leads.get(email)
        return {"id": x["id"], "email": email} if x else {}

    def lead_campaigns(self, lead_id):
        return [{"email_campaign_id": str(x["campaign"])} for x in self.leads.values() if x["id"] == lead_id]

    def unsubscribe_everywhere(self, lead_id):
        self.unsub.add(lead_id)
        return {"ok": True}

    def block(self, items):
        self.blocked += items
        return {"ok": True}

    def set_category(self, cid, lead_id, cat, pause=False):
        self.categories[lead_id] = (cid, cat, pause)

    def inbox_replies(self, offset=0, limit=20, campaign_ids=None, since_iso=None, until_iso=None):
        return {"messages": self.replies[offset:offset + limit]}

    def email_accounts(self):
        return [{"id": 1, "from_email": "buda@trysmartemploy.com", "message_per_day": 15, "daily_sent_count": 4,
                 "is_smtp_success": True, "is_imap_success": True,
                 "warmup_details": {"status": "ACTIVE", "warmup_reputation": "100%"}}]

    def mailbox_health(self, start, end, campaign_ids=None):
        return [{"email_account": "buda@trysmartemploy.com", "sent": 200, "bounced": 8}]

    def test(self):
        return {"ok": True}


def seed(store, cities=5, per_city=10, extra=None):
    """Agencies in several cities, each with a verified director and enough verified hot leads in its city."""
    n = 0
    for ci in range(cities):
        city = f"Town{ci}"
        sid = store.val("insert into searches (label, keywords, location) values (%s, '{x}', %s) returning id",
                        (f"IT & Tech - {city}", city))
        for li in range(per_city * 2 + 2):
            key, emp = f"{city}-l{li}", f"Employer {ci}-{li}"
            store.q("""insert into hot_leads (lead_key, employer_name, employer_norm, title, posting_count_90d, first_seen_at,
                       last_seen_at, live_now, long_open, score, job_ids) values (%s, %s, %s, 'Software Engineer', 3, '2026-08-01',
                       now(), true, false, %s, '{}')""", (key, emp, emp.lower(), 100 - li))
            store.q("insert into hot_lead_searches values (%s, %s)", (key, sid))
            store.q("insert into lead_contacts (employer_norm, company_name, verified_company) values (%s, %s, true)",
                    (emp.lower(), emp))
        for ai in range(per_city):
            n += 1
            aid = store.val("""insert into agencies (name, name_norm, ch_company_type, ch_company_status, ch_match_confidence,
                               ch_checked_at, ads_count_90d) values (%s, %s, 'ltd', 'active', 1, now(), %s) returning id""",
                            (f"Agency {n}", f"agency {n}", 100 - n))
            store.q("insert into agency_searches values (%s, %s, 3)", (aid, sid))
            store.q("""insert into contacts (agency_id, full_name, first_name, role, email, email_verified, source)
                       values (%s, %s, %s, 'Director', %s, true, 'companies_house')""",
                    (aid, f"Pat Person{n}", "Pat", f"pat{n}@agency{n}.co.uk"))
    store.set_setting(OR.S_LEADERS, 11)
    store.set_setting(OR.S_SALES, 22)


@pytest.fixture
def sl():
    return FakeSmartlead()


@pytest.fixture
def api(store, sl):
    hooks = Hooks(collect_one=lambda *a: None, collect_all=lambda *a: None, enrich=lambda *a: {}, role_services=lambda *a: None,
                  post_webhook=lambda *a: None, make_smartlead=lambda st: sl)
    return TestClient(create_app(Settings(engine_api_secret="s", smartlead_api_key=KEY, owner_test_email="me@roberts.test"),
                                 store=store, hooks=hooks))


def test_build_queue_gives_50_full_rows_and_never_reuses_a_lead_in_a_city(store, sl):
    seed(store, cities=6, per_city=10)
    rows = OR.build_queue(store, Settings(), sl, limit=50, today=TODAY)
    good = [r for r in rows if r["status"] == "built"]
    assert len(good) == 50
    for r in good:
        p = OR.preview(store, sl, r)
        text = p["steps"][0]["subject"] + p["steps"][0]["body"]
        assert "{{" not in text and "Hi Pat," in text and r["fields"]["lead2_employer"] in text
    by_city = {}
    for r in good:
        for k in r["fields"]["_lead_keys"]:
            assert k not in by_city.setdefault(r["fields"]["city"], set())
            by_city[r["fields"]["city"]].add(k)


def test_blocked_rows_are_marked_and_never_pushed(store, sl):
    seed(store, cities=1, per_city=4)
    store.q("update contacts set email_verified = false where email like 'pat1@%%'")
    store.q("update agencies set ch_company_type = 'not_found' where name = 'Agency 2'")
    from app.pg import email_hash
    store.suppress("pat3@agency3.co.uk")
    rows = OR.build_queue(store, Settings(), sl, today=TODAY)
    reasons = {r["agency_name"]: r["warnings"] for r in rows if r["status"] == "blocked"}
    assert "email not verified" in reasons["Agency 1"] and "not a confirmed Ltd/LLP/PLC" in reasons["Agency 2"]
    assert "on the suppression list" in reasons["Agency 3"]
    res = OR.push(store, sl, [r["id"] for r in rows])
    pushed = [c[2] for c in sl.calls if c[0] == "add"]
    assert pushed == ["pat4@agency4.co.uk"] and sum(1 for x in res if x["ok"]) == 1
    assert store.one("select stage from agencies where name = 'Agency 4'")["stage"] == "contacted"
    assert store.val("select count(*) from activities where notes like 'cold email queued%%'") == 1
    assert store.val("select count(*) from lead_assignments") == 2


def test_empty_variable_and_already_in_smartlead_block(store, sl):
    seed(store, cities=1, per_city=2)
    store.q("update contacts set first_name = '', full_name = '' where email like 'pat1@%%'")
    sl.leads["pat2@agency2.co.uk"] = {"id": 5, "campaign": 99, "lead": {}}
    rows = {r["agency_name"]: r for r in OR.build_queue(store, Settings(), sl, today=TODAY)}
    assert rows["Agency 1"]["status"] == "blocked" and any("first_name" in w for w in rows["Agency 1"]["warnings"])
    assert "already in a Smartlead campaign" in rows["Agency 2"]["warnings"]


def test_job_titles_pick_the_campaign():
    leaders = ["Director", "", "Founder & Sales Director", "CEO", "Managing Director", "Sales Manager", "Branch Manager",
               "Head of Recruitment", "Managing Partner", "Owner"]
    sales = ["Recruitment Consultant", "Senior Recruitment Consultant", "Recruiter", "Resourcer", "Account Manager",
             "Business Development Manager", "BDM", "Talent Acquisition Partner", "Sales Executive"]
    assert {t: OR.audience(t) for t in leaders} == {t: "leaders" for t in leaders}
    assert {t: OR.audience(t) for t in sales} == {t: "sales" for t in sales}
    assert OR.audience("Office Administrator") == "other"          # nobody else is cold-emailed


def add_sales_person(store, agency_name, email="sam@x.co.uk", title="Recruitment Consultant"):
    aid = store.val("select id from agencies where name = %s", (agency_name,))
    store.q("""insert into contacts (agency_id, full_name, first_name, role, email, email_verified, source)
               values (%s, 'Sam Seller', 'Sam', %s, %s, true, 'website')""", (aid, title, email))


def test_an_agency_gets_one_row_per_campaign_with_the_same_leads(store, sl):
    seed(store, cities=1, per_city=2)
    add_sales_person(store, "Agency 1")
    store.q("""insert into contacts (agency_id, full_name, first_name, role, email, email_verified, source)
               select id, 'Ann Admin', 'Ann', 'Office Administrator', 'ann@x.co.uk', true, 'website' from agencies where name = 'Agency 1'""")
    rows = [r for r in OR.build_queue(store, Settings(), sl, today=TODAY) if r["agency_name"] == "Agency 1"]
    assert sorted((r["campaign_role"], r["fields"]["_email"], r["status"]) for r in rows) == [
        ("leaders", "pat1@agency1.co.uk", "built"), ("sales", "sam@x.co.uk", "built")]
    assert rows[0]["fields"]["_lead_keys"] == rows[1]["fields"]["_lead_keys"]
    res = OR.push(store, sl, [r["id"] for r in rows])
    assert [x["ok"] for x in res] == [True, True]
    assert {c[1] for c in sl.calls if c[0] == "add"} == {11, 22}      # leaders -> 11, sales -> 22


def test_a_reply_from_one_person_pauses_their_colleague(store, sl):
    seed(store, cities=1, per_city=1)
    add_sales_person(store, "Agency 1")
    rows = OR.build_queue(store, Settings(), sl, today=TODAY)
    OR.push(store, sl, [r["id"] for r in rows])
    store.q("""insert into outreach_replies (smartlead_message_id, agency_id, contact_id, lead_email, smartlead_campaign_id)
               select 'm1', agency_id, id, email, 22 from contacts where email = 'sam@x.co.uk'""")
    out = OR.categorise(store, sl, store.val("select id from outreach_replies"), "interested", today=TODAY)
    pat, sam = sl.leads["pat1@agency1.co.uk"]["id"], sl.leads["sam@x.co.uk"]["id"]
    assert sl.categories[pat] == (11, 1, True)       # colleague in the founders campaign: paused
    assert sl.categories[sam] == (22, 1, False)      # the person who replied: categorised
    assert any("colleague pat1@agency1.co.uk paused" in x for x in out["smartlead"])


def test_one_lead_and_row_actions(store, sl):
    seed(store, cities=1, per_city=1)
    store.q("delete from hot_lead_searches where lead_key not in ('Town0-l0')")
    add_sales_person(store, "Agency 1")
    rows = {r["campaign_role"]: r for r in OR.build_queue(store, Settings(), sl, today=TODAY)}
    # the founders email uses lead2_*, so one lead can't fill it; the sales email only needs lead 1
    assert rows["leaders"]["status"] == "blocked" and "only 1 hot lead" in rows["leaders"]["warnings"][0]
    r = rows["sales"]
    assert r["status"] == "built"
    OR.move(store, r["id"], "leaders")
    assert OR.row(store, r["id"])["campaign_role"] == "leaders"
    store.q("insert into hot_lead_searches select 'Town0-l1', search_id from hot_lead_searches limit 1")
    s = OR.swap_lead(store, r["id"], 1)
    assert s["fields"]["lead1_employer"] == "Employer 0-1"
    OR.remove(store, r["id"])
    assert OR.push(store, sl, [r["id"]])[0]["ok"] is False and not [c for c in sl.calls if c[0] == "add"]


def test_test_push_sends_exactly_one_lead_to_the_owner_with_every_variable(api, store, sl):
    seed(store, cities=1, per_city=3)
    api.post("/outreach/queue/build", headers=H)
    r = api.post("/outreach/queue/test-push", headers=H).json()
    adds = [c for c in sl.calls if c[0] == "add"]
    assert r["ok"] and len(adds) == 1 and adds[0][2] == "me@roberts.test"
    lead = sl.leads["me@roberts.test"]["lead"]
    assert all(str(v).strip() for v in lead["custom_fields"].values()) and lead["first_name"] == "Pat"
    assert {"lead1_employer", "lead2_since", "niche", "city"} <= set(lead["custom_fields"])
    assert store.val("select count(*) from agencies where stage = 'contacted'") == 0     # no agency touched


def test_replies_sync_and_unsubscribe_removes_everywhere(api, store, sl):
    seed(store, cities=1, per_city=2)
    rows = api.post("/outreach/queue/build", headers=H).json()
    api.post("/outreach/queue/push", headers=H, json={"ids": [r["id"] for r in rows]})
    sl.replies = [{"id": "m1", "campaign_lead_map_id": "77", "lead": {"email": "pat1@agency1.co.uk"},
                   "campaign": {"id": 11}, "last_message": {"id": "e1", "subject": "Re: leads", "body": "<p>Take me off your list</p>",
                                                            "received_at": "2026-11-06T09:00:00Z"}}]
    assert api.post("/outreach/replies/sync", headers=H).json()["new"] == 1
    assert api.post("/outreach/replies/sync", headers=H).json()["new"] == 0        # same message never twice
    rep = api.get("/outreach/replies", headers=H).json()[0]
    assert rep["agency_name"] == "Agency 1" and rep["snippet"] == "Take me off your list"
    out = api.post(f"/outreach/replies/{rep['id']}/categorise", headers=H, json={"category": "unsubscribe"}).json()
    lead_id = sl.leads["pat1@agency1.co.uk"]["id"]
    assert lead_id in sl.unsub and "pat1@agency1.co.uk" in sl.blocked and sl.categories[lead_id][1] == 4
    assert store.one("select do_not_contact from contacts where email = 'pat1@agency1.co.uk'")["do_not_contact"]
    assert store.one("select stage from agencies where name = 'Agency 1'")["stage"] == "do_not_contact"
    assert store.is_suppressed("pat1@agency1.co.uk") and out["smartlead"]


def test_interested_and_not_now(store, sl):
    seed(store, cities=1, per_city=2)
    store.q("""insert into outreach_replies (smartlead_message_id, agency_id, contact_id, lead_email, smartlead_campaign_id)
               select 'x' || id, agency_id, id, email, 11 from contacts""")
    ids = [r["id"] for r in store.q("select id from outreach_replies order by id")]
    OR.categorise(store, sl, ids[0], "interested", today=TODAY)
    OR.categorise(store, sl, ids[1], "not_now", today=TODAY)
    a1, a2 = store.q("select stage, next_follow_up from agencies order by id")
    assert a1["stage"] == "replied" and a1["next_follow_up"] == TODAY + dt.timedelta(days=2)
    assert a2["next_follow_up"] == TODAY + dt.timedelta(days=90)
    assert store.val("select count(*) from activities where notes like 'Task: Send 20 leads%%'") == 1


def test_bounce_over_3_percent_in_last_200_sends_auto_pauses(store, sl):
    store.set_setting(OR.S_LEADERS, 11)
    rows = [{"sent_time": f"2026-11-0{1 + i % 5}T{10 + i % 9}:00:{i % 60:02d}Z", "is_bounced": i < 7} for i in range(200)]
    sl.stats_rows[11] = rows
    h = OR.health(store, sl, today=TODAY)
    c = h["campaigns"][0]
    assert c["red"] and c["auto_paused"] and sl.status[11] == "PAUSED" and c["bounce_rate"] == 0.035
    assert h["inboxes"][0]["red"]                       # 8 / 200 = 4% for the inbox too
    OR.health(store, sl, today=TODAY)                    # not paused twice the same day
    assert [c for c in sl.calls if c[0] == "status"] == [("status", 11, "PAUSED")]
    sl.stats_rows[11] = [{"sent_time": "2026-11-01T10:00:00Z", "is_bounced": i < 2} for i in range(200)]
    store.q("delete from http_cache where key like 'outreach:autopaused:%%'")
    assert not OR.health(store, sl, today=TODAY)["campaigns"][0]["red"]   # 1% is fine


def test_stats_campaigns_and_settings_api(api, store, sl):
    seed(store, cities=1, per_city=1)
    st = api.get("/outreach/stats?refresh=true", headers=H).json()
    assert st["today"]["sent"] == 200 and st["today"]["bounce_rate"] == 0.03 and st["today"]["reply_rate"] == 0.03
    cs = api.get("/outreach/campaigns", headers=H).json()
    assert [(c["id"], c["role"], c["inboxes"]) for c in cs] == [(11, "leaders", 2), (22, "sales", 2)]
    api.post("/outreach/campaigns/11/pause", headers=H)
    assert sl.status[11] == "PAUSED"
    s = api.patch("/outreach/settings", headers=H, json={"daily_push_limit": 25, "auto_push": True}).json()
    assert s["daily_push_limit"] == 25 and s["auto_push"] is True and s["key_set"] is True
    assert api.post("/outreach/test-connection", headers=H).json()["ok"]


def test_api_key_never_reaches_responses_or_logs(store, caplog):
    import httpx
    from app.smartlead import Smartlead, SmartleadError

    def handler(req):
        assert req.url.params["api_key"] == KEY
        return httpx.Response(500, text=f"boom for {req.url}")      # Smartlead echoing the URL back
    sl = Smartlead(KEY, transport=httpx.MockTransport(handler), sleep=lambda s: None)
    caplog.set_level(logging.INFO)
    with pytest.raises(SmartleadError) as e:
        sl.campaigns()
    assert KEY not in str(e.value) and "***" in str(e.value)
    assert KEY not in caplog.text
    from app.config import RedactSecrets
    assert KEY in Settings(smartlead_api_key=KEY).secret_values()


def test_auto_push_only_when_switched_on(store, sl):
    seed(store, cities=1, per_city=2)
    assert OR.auto_push(store, Settings(), sl) == {"skipped": "auto-push is off"}
    store.set_setting(OR.S_AUTO, True)
    assert OR.auto_push(store, Settings(), sl)["pushed"] == 2


def test_scheduler_due():
    from zoneinfo import ZoneInfo

    from app.scheduler import due
    uk = ZoneInfo("Europe/London")
    t = dt.datetime(2026, 11, 6, 7, 31, tzinfo=uk)
    assert due(t, None, 7, 30) and not due(t, t.date(), 7, 30) and not due(t.replace(hour=7, minute=10), None, 7, 30)


def test_prospect_csv_adds_people_to_tracked_agencies_by_title(api, store, sl):
    seed(store, cities=1, per_city=2)
    store.q("update agencies set website = 'https://www.agency2.co.uk' where name = 'Agency 2'")
    csv_text = ("First Name,Last Name,Job Title,Email,Email Status,Company Name,Company Website,LinkedIn URL\n"
                "Sam,Seller,Senior Recruitment Consultant,sam@agency1.co.uk,Verified,Agency 1 Ltd,,https://linkedin.com/in/sam\n"
                "Pat,Person1,Managing Director,pat1@agency1.co.uk,Verified,Agency 1,,\n"         # already there: updated
                "Bo,Boss,Founder,bo@agency2.co.uk,Verified,Agency Two Recruitment,agency2.co.uk,\n"  # matched by website
                "Ann,Admin,Office Administrator,ann@agency1.co.uk,Verified,Agency 1,,\n"         # not emailed
                "Ria,Risky,Recruiter,ria@agency1.co.uk,Catch-all,Agency 1,,\n"                 # added, not verified
                "Zed,Elsewhere,Director,zed@other.co.uk,Verified,Some Other Agency,,\n")
    r = api.post("/outreach/import-contacts", headers=H, json={"csv": csv_text}).json()
    assert (r["added"], r["updated"], r["skipped_title"], r["unverified"]) == (3, 1, 1, 1)
    assert r["by_campaign"] == {"leaders": 2, "sales": 2} and r["not_tracked"] == ["Some Other Agency"]
    sam = store.one("select * from contacts where email = 'sam@agency1.co.uk'")
    assert sam["email_verified"] and sam["role"] == "Senior Recruitment Consultant" and sam["source"] == "prospect"
    assert not store.one("select email_verified from contacts where email = 'ria@agency1.co.uk'")["email_verified"]
    rows = [x for x in OR.build_queue(store, Settings(), sl, today=TODAY) if x["agency_name"] == "Agency 1"]
    assert {(x["campaign_role"], x["fields"]["_email"]) for x in rows if x["status"] == "built"} == {
        ("leaders", "pat1@agency1.co.uk"), ("sales", "sam@agency1.co.uk")}
    bad = api.post("/outreach/import-contacts", headers=H, json={"csv": "Name,Phone\nx,1\n"})
    assert bad.status_code == 422 and "email column" in bad.json()["detail"]
