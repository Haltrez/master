"""The project agent: a scripted Claude drives fake research tools to a submitted result."""

import json
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import Hooks, create_app
from tests.test_orders import Block, FakeClaude, resp

H = {"X-API-Key": "s"}
BRIEF = "TASK: Build a hot-lead list for a prospect agency. M&E, Manchester, 25 miles."


class FakeResearch:
    def __init__(self):
        self.calls = []

    def search_jobs(self, **a):
        self.calls.append(("search_jobs", a))
        return {"direct_ads": [{"id": "reed:1", "company": "DL M&E", "title": "Electrical Project Manager"}], "agency_ads": 4}

    def check_link(self, **a):
        self.calls.append(("check_link", a))
        return {"live": True}


def app_with(store, script):
    claude = FakeClaude(script)
    research = FakeResearch()
    hooks = Hooks(collect_one=lambda *a: None, collect_all=lambda *a: None, enrich=lambda *a: {}, role_services=lambda *a: None,
                  post_webhook=lambda *a: None, make_claude=lambda st: claude, make_research=lambda s, st: research,
                  run_in_thread=False)
    return TestClient(create_app(Settings(engine_api_secret="s"), store=store, hooks=hooks)), claude, research


SUBMIT = {"columns": ["Candidate role", "Company", "Ad link"], "rows": [["Electrical PM", "DL M&E", "https://x/1"]],
          "summary_lines": ["Market demand: 4 agency ads"], "email_subject": "Leads", "email_body": "Hi Jamie", "note": "thin"}


def test_project_runs_tools_and_submits(store):
    c, claude, research = app_with(store, [
        resp("tool_use", Block(type="text", text="Searching first."),
             Block(type="tool_use", id="t1", name="search_jobs", input={"titles": ["electrical project manager"], "location": "Manchester"})),
        resp("tool_use", Block(type="tool_use", id="t2", name="check_link", input={"url": "https://x/1"}),
             Block(type="tool_use", id="t3", name="submit_results", input=SUBMIT)),
        resp("end_turn", Block(type="text", text="Done: 1 lead.")),
    ])
    p = c.post("/projects", headers=H, json={"brief": BRIEF}).json()
    p = c.get(f"/projects/{p['id']}", headers=H).json()
    assert p["status"] == "done" and p["result"]["rows"] == SUBMIT["rows"]
    assert [x[0] for x in research.calls] == ["search_jobs", "check_link"]
    texts = [l["text"] for l in p["log"]]
    assert "Searching job boards: electrical project manager around Manchester" in texts and "Finished" in texts
    csv_text = c.get(f"/projects/{p['id']}/result.csv", headers=H).text
    assert "DL M&E" in csv_text and "Market demand: 4 agency ads" in csv_text
    # the brief went in as the first message, tool results came back in order
    assert BRIEF in claude.requests[0][0]["content"][0]["text"]
    assert claude.requests[1][2]["content"][0]["tool_use_id"] == "t1"
    assert c.get("/projects", headers=H).json()[0]["has_result"] is True


def test_follow_up_continues_the_same_conversation(store):
    c, claude, _ = app_with(store, [
        resp("tool_use", Block(type="tool_use", id="t1", name="submit_results", input=SUBMIT)),
        resp("end_turn", Block(type="text", text="Done.")),
        resp("tool_use", Block(type="tool_use", id="t2", name="submit_results", input={**SUBMIT, "rows": []})),
        resp("end_turn", Block(type="text", text="Removed it.")),
    ])
    pid = c.post("/projects", headers=H, json={"brief": BRIEF}).json()["id"]
    c.post(f"/projects/{pid}/message", headers=H, json={"message": "drop DL M&E"})
    p = c.get(f"/projects/{pid}", headers=H).json()
    assert p["result"]["rows"] == [] and len(claude.requests[2]) == 5   # history + the follow-up


def test_project_without_submit_is_an_error_and_short_briefs_are_refused(store):
    c, _, _ = app_with(store, [resp("end_turn", Block(type="text", text="I can't."))])
    assert c.post("/projects", headers=H, json={"brief": "hi"}).status_code == 422
    pid = c.post("/projects", headers=H, json={"brief": BRIEF}).json()["id"]
    p = c.get(f"/projects/{pid}", headers=H).json()
    assert p["status"] == "error" and "without submitting" in p["error"]


def test_excel_downloads(store):
    import io
    from openpyxl import load_workbook
    c, _, _ = app_with(store, [resp("tool_use", Block(type="tool_use", id="t1", name="submit_results", input=SUBMIT)),
                               resp("end_turn", Block(type="text", text="Done."))])
    pid = c.post("/projects", headers=H, json={"brief": BRIEF}).json()["id"]
    r = c.get(f"/projects/{pid}/result.xlsx", headers=H)
    assert r.status_code == 200 and "spreadsheetml" in r.headers["content-type"]
    wb = load_workbook(io.BytesIO(r.content))
    ws = wb["Leads"]
    assert [c.value for c in ws[1]] == SUBMIT["columns"] and ws["B2"].value == "DL M&E"
    assert ws["C2"].hyperlink.target == "https://x/1" and ws.freeze_panes and wb.sheetnames == ["Leads"]   # client copy
    full = load_workbook(io.BytesIO(c.get(f"/projects/{pid}/result.xlsx?notes=true", headers=H).content))
    assert full["Note (private)"]["A1"].value == "thin" and "Email" in full.sheetnames


def test_linkedin_links_must_come_from_search_and_header_lines_go_on_top(store):
    import io
    from openpyxl import load_workbook
    from app.projects import _clean_linkedin
    r = SimpleNamespace(seen_urls={"https://uk.linkedin.com/in/ben-milner-0b388974"})
    rows, fixed = _clean_linkedin(r, [["DL", "https://uk.linkedin.com/in/ben-milner-0b388974/"], ["Sudlows", "uk.linkedin.com/in/zac"]])
    assert rows == [["DL", "https://uk.linkedin.com/in/ben-milner-0b388974/"], ["Sudlows", "unknown"]] and fixed == 1
    sub = {**SUBMIT, "header_lines": ["DL M&E and Sudlows say no agencies on their careers pages."]}
    c, _, _ = app_with(store, [resp("tool_use", Block(type="tool_use", id="t1", name="submit_results", input=sub)),
                               resp("end_turn", Block(type="text", text="Done."))])
    pid = c.post("/projects", headers=H, json={"brief": BRIEF}).json()["id"]
    ws = load_workbook(io.BytesIO(c.get(f"/projects/{pid}/result.xlsx", headers=H).content))["Leads"]
    assert ws["A1"].value.startswith("DL M&E and Sudlows") and [x.value for x in ws[3]] == SUBMIT["columns"]
    assert c.get(f"/projects/{pid}/result.csv", headers=H).text.lstrip("﻿").startswith("DL M&E and Sudlows")


def test_careers_pages_saying_no_agencies_are_flagged():
    from app.research_tools import NO_AGENCIES
    assert NO_AGENCIES.search("Job board - No agencies please")
    assert NO_AGENCIES.search("Our CV route is not open to third-party recruiters.")
    assert NO_AGENCIES.search("Recruitment is handled by our in-house recruitment team")
    assert not NO_AGENCIES.search("We are an employment agency friendly business park")


def test_project_cost_is_recorded_from_usage(store):
    from app.projects import cost_of
    usage = SimpleNamespace(input_tokens=1000, output_tokens=2000, cache_read_input_tokens=100_000, cache_creation_input_tokens=5000)
    r1 = resp("tool_use", Block(type="tool_use", id="t1", name="submit_results", input=SUBMIT)); r1.usage = usage
    r2 = resp("end_turn", Block(type="text", text="Done.")); r2.usage = usage
    c, _, _ = app_with(store, [r1, r2])
    pid = c.post("/projects", headers=H, json={"brief": BRIEF}).json()["id"]
    p = c.get(f"/projects/{pid}", headers=H).json()
    one = cost_of("claude-opus-5-5", 1000, 5000, 100_000, 2000)
    assert abs(float(p["cost_usd"]) - 2 * one) < 0.0001 and p["cache_read_tokens"] == 200_000
    assert round(one, 4) == round((1000 * 4 + 5000 * 5 + 100_000 * 0.2 + 2000 * 20) / 1e6, 4)


def test_house_style_is_given_to_new_projects(store):
    sub = {**SUBMIT, "header_lines": ["DL M&E says no agencies."]}
    c, claude, _ = app_with(store, [
        resp("tool_use", Block(type="tool_use", id="t1", name="submit_results", input=sub)), resp("end_turn", Block(type="text", text="Done.")),
        resp("end_turn", Block(type="text", text="I can't."))])
    pid = c.post("/projects", headers=H, json={"brief": BRIEF}).json()["id"]
    st = c.post(f"/projects/{pid}/use-as-style", headers=H).json()
    assert st["header_lines"] == ["DL M&E says no agencies."] and c.get("/projects-style", headers=H).json()["from_project"] == pid
    c.post("/projects", headers=H, json={"brief": BRIEF + " Second one."})
    first_msg = claude.requests[2][0]["content"][0]["text"]
    assert "HOUSE STYLE" in first_msg and "DL M&E says no agencies." in first_msg


def test_fetch_page_reads_public_ats_json_and_web_search_can_target_the_us(store):
    import httpx
    from app.config import Settings
    from app.research_tools import Research

    class Fetcher:
        def get(self, url, budget):
            return httpx.Response(200, headers={"content-type": "application/json"},
                                  json={"jobs": [{"id": 4011, "title": "Head of BSA/AML", "updated_at": "2026-10-30"}]},
                                  request=httpx.Request("GET", url))
    seen = {}

    def brave(req):
        seen["country"] = req.url.params["country"]
        return httpx.Response(200, json={"web": {"results": []}})
    r = Research(store, Settings(brave_api_key="b"), fetcher=Fetcher(), http=httpx.Client(transport=httpx.MockTransport(brave)))
    page = r.fetch_page("https://boards-api.greenhouse.io/v1/boards/acme/jobs")
    assert "Head of BSA/AML" in page["text"] and "4011" in page["text"]
    r.web_search("Acme Bank consent order OCC", country="us")
    assert seen["country"] == "us"
