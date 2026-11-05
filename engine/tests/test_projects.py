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
