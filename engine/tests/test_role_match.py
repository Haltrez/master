import csv
import datetime as dt
import re
from pathlib import Path

import pytest

from app.config import Settings
from app.geo import Geocoder
from app.polite import PoliteFetcher
from app.reed_client import ReedClient
from app.role_match import (BriefError, Services, add_brief, check_employers, import_briefs, run_match, write_csv)
from app.role_repo import LocalRoleRepo
from app.wayback import Wayback
from tests.conftest import TEST_KEY, FakeReed
from tests.role_fakes import Clock, FakeWeb

ROOT = Path(__file__).resolve().parents[2]
AGENCY = "MCM Recruitment Solutions"


def services(web, clock, reed=None, path=None):
    repo = LocalRoleRepo(path, clock=clock)
    t = web.transport
    return Services(repo=repo, settings=Settings(reed_api_key=TEST_KEY, reed_requests_per_second=0),
                    fetcher=PoliteFetcher(transport=t, sleep=clock.sleep, clock=clock.monotonic),
                    geocoder=Geocoder(repo, transport=t, sleep=clock.sleep, clock=clock.monotonic),
                    wayback=Wayback(repo, transport=t, sleep=clock.sleep, clock=clock.monotonic),
                    reed=reed, clock=clock)


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def web():
    return FakeWeb(wayback={"https://www.dl-bsl.co.uk/jobs/electrical-project-manager": "20260815120000"})


def matched(report):
    return {(m.brief["label"], m.employer["name"], m.job["title"]) for m in report.rows}


def test_seed_employers_checked_politely(web, clock):
    svc = services(web, clock)
    results = check_employers(svc)
    assert [(r.name, r.outcome) for r in results] == [
        ("DL M&E Building Services Ltd", "checked"), ("Central Electrical & Mechanical NW Ltd", "checked"),
        ("B-engineering (NW) Ltd", "checked")]
    assert all(r.pages <= 3 for r in results)
    jobs = svc.repo.jobs()
    assert {"Electrical Project Manager", "Electrical Estimator", "Senior Mechanical Estimator"} <= {j["title"] for j in jobs}
    # Only the allowed fields are stored for each job.
    allowed = {"id", "employer_id", "source", "title", "title_norm", "location_text", "lat", "lng", "url",
               "first_seen_at", "last_seen_at", "wayback_first_seen", "page_hash", "date_posted", "keywords_found"}
    assert all(set(j) <= allowed for j in jobs)
    # robots.txt fetched before any page on every domain
    for host in ("www.dl-bsl.co.uk", "celectrical.com", "b-enggroup.co.uk"):
        assert web.hits(host)[0].endswith("/robots.txt")
    # Breathe HR's robots.txt allows /v/ vacancy pages (longest match), so one was read as a detail page
    assert any("/v/" in u for u in web.hits("hr.breathehr.com"))


def test_check_at_most_once_a_day_default_weekly(web, clock):
    svc = services(web, clock)
    check_employers(svc)
    n = len(web.requests)
    clock.advance(hours=3)
    assert {r.outcome for r in check_employers(svc, force=True)} == {"skipped"}   # never twice a day
    clock.advance(days=2)
    assert {r.outcome for r in check_employers(svc)} == {"skipped"}               # weekly by default
    assert len(web.requests) == n
    assert {r.outcome for r in check_employers(svc, force=True)} == {"checked"}   # --force after a day
    clock.advance(days=7)
    assert {r.outcome for r in check_employers(svc)} == {"checked"}


def test_robots_blocked_employer(clock):
    web = FakeWeb(robots={"celectrical.com": "User-agent: *\nDisallow: /careers/\n",
                          "www.dl-bsl.co.uk": "", "b-enggroup.co.uk": ""})
    svc = services(web, clock)
    res = {r.name: r for r in check_employers(svc)}
    assert res["Central Electrical & Mechanical NW Ltd"].outcome == "blocked_by_robots"
    emp = next(e for e in svc.repo.employers() if e["name"].startswith("Central"))
    assert emp["status"] == "blocked_by_robots"
    assert not any("/careers" in u for u in web.hits("celectrical.com"))


def test_js_only_page_marked_needs_manual(clock):
    web = FakeWeb(pages={"https://celectrical.com/careers/":
                         '<html><body><div id="root"></div><script src="/a.js"></script></body></html>'})
    svc = services(web, clock)
    check_employers(svc)
    emp = next(e for e in svc.repo.employers() if e["name"].startswith("Central"))
    assert emp["status"] == "needs_manual"


def test_six_brief_yaml_end_to_end(web, clock, tmp_path):
    svc = services(web, clock)
    briefs = import_briefs(svc, ROOT / "briefs.example.yaml")
    assert len(briefs) == 6 and all(b["lat"] is not None for b in briefs)
    check_employers(svc)
    report = run_match(svc, AGENCY, use_reed=False)
    got = matched(report)
    assert ("Electrical Project Manager - Manchester", "DL M&E Building Services Ltd", "Electrical Project Manager") in got
    assert ("Electrical Estimator - Warrington", "Central Electrical & Mechanical NW Ltd", "Electrical Estimator") in got
    assert ("Electrical Site Manager - Manchester", "Central Electrical & Mechanical NW Ltd", "Electrical Site Supervisor") in got
    # Cardiff roles are far outside every radius
    assert not any("Cardiff" in (m.job.get("location_text") or "") for m in report.rows)

    out = tmp_path / "matches.csv"
    write_csv(report, out)
    rows = list(csv.reader(out.open(encoding="utf-8")))
    assert rows[0] == ["candidate_role", "company", "their_open_role", "location", "distance_miles", "open_since",
                       "signal", "link", "source", "score"]
    body = [r for r in rows[1:] if r and r[1]]
    # sorted by candidate role, then score (desc)
    keys = [(r[0].lower(), -int(r[9])) for r in body]
    assert keys == sorted(keys)
    epm = next(r for r in body if r[2] == "Electrical Project Manager")
    assert epm[5] == "Aug 2026"                   # Wayback capture shown as open since
    assert "Open since" not in epm[6]             # being open a long time isn't a signal any more (evergreen risk)
    assert epm[7] == "https://www.dl-bsl.co.uk/jobs/electrical-project-manager"
    ce = next(r for r in body if r[2] == "Electrical Estimator")
    assert ce[5] == "unknown"                     # no capture -> unknown, never a guess
    assert ce[3] == "Manchester (company base)"
    summary = [r for r in rows[1:] if r and not r[1]]
    assert len(summary) == 6 and all(r[6].startswith("Market demand") for r in summary)
    text = out.read_text(encoding="utf-8")
    assert not re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", text)     # no emails anywhere in the export
    assert "#" not in "".join(r[7] for r in body)             # synthetic anchors never shown


def test_match_status_survives_rematch(web, clock):
    svc = services(web, clock)
    import_briefs(svc, ROOT / "briefs.example.yaml")
    check_employers(svc)
    run_match(svc, AGENCY, use_reed=False)
    m = svc.repo.t["brief_matches"][0]
    m["status"] = "sent"
    first = m["first_matched_at"]
    n = len(svc.repo.t["brief_matches"])
    clock.advance(days=1)
    run_match(svc, AGENCY, use_reed=False)
    assert len(svc.repo.t["brief_matches"]) == n
    assert svc.repo.t["brief_matches"][0]["status"] == "sent"
    assert svc.repo.t["brief_matches"][0]["first_matched_at"] == first


def test_reed_agency_ads_are_demand_not_matches(web, clock, sample):
    base = sample["results"][0]
    emp_ad = {**base, "jobId": 91, "employerName": "Mersey Building Services Ltd", "jobTitle": "Electrical Estimator",
              "locationName": "Warrington", "jobUrl": "https://www.reed.co.uk/jobs/electrical-estimator/91"}
    agency_ads = [{**base, "jobId": 100 + i, "employerName": f"Agency {i} Recruitment", "jobTitle": "Electrical Estimator",
                   "locationName": "Warrington"} for i in range(9)]
    disguised = {**base, "jobId": 200, "employerName": "North West Talent Partners", "jobTitle": "Electrical Estimator",
                 "locationName": "Warrington"}
    off_role = {**base, "jobId": 300, "employerName": "Acme Agency", "jobTitle": "Credit Controller"}
    fake = FakeReed({("electrical estimator", "employer"): [emp_ad, disguised],
                     ("electrical estimator", "agency"): agency_ads + [off_role]})
    reed = ReedClient(TEST_KEY, transport=fake.transport, requests_per_second=0, sleep=lambda s: None)
    svc = services(web, clock, reed=reed)
    add_brief(svc, AGENCY, "Electrical Estimator", "Warrington", 30, ["m&e"])
    check_employers(svc)
    report = run_match(svc, AGENCY)
    companies = {m.employer["name"] for m in report.rows}
    assert "Mersey Building Services Ltd" in companies
    assert not any("Recruitment" in c or "Talent" in c or "Agency" in c for c in companies)
    d = next(iter(report.demand.values()))
    assert d.count == 10   # 9 agency ads + 1 employer-query ad reclassified by the agency-name rule
    # Reed employer joins the directory as a research-queue row
    emp = next(e for e in svc.repo.employers() if e["name"] == "Mersey Building Services Ltd")
    assert (emp["source"], emp["status"]) == ("reed", "research")
    reed_row = next(m for m in report.rows if m.employer["name"] == "Mersey Building Services Ltd")
    assert reed_row.job["source"] == "reed"
    # queried with posted_by both ways, keyword = the exact title, radius 1.5x
    q = [r.url.params for r in fake.requests]
    assert {p["distanceFromLocation"] for p in q} == {"45"}


def test_local_store_persists(web, clock, tmp_path):
    path = tmp_path / "store.json"
    svc = services(web, clock, path=path)
    add_brief(svc, AGENCY, "Electrical Estimator", "Warrington", 30)
    svc.repo.save()
    again = LocalRoleRepo(path, clock=clock)
    assert len(again.briefs(again.agency_by_name(AGENCY)["id"])) == 1
    assert len(again.employers()) == 3


def test_brief_dedupes_and_unknown_role(web, clock):
    svc = services(web, clock)
    a = add_brief(svc, AGENCY, "Electrical Estimator", "Warrington", 30)
    b = add_brief(svc, AGENCY, "electrical estimator", "warrington", 40)
    assert a["id"] == b["id"] and b["radius_miles"] == 40
    with pytest.raises(BriefError, match="Known roles"):
        add_brief(svc, AGENCY, "Plumber", "Warrington")


@pytest.mark.parametrize("yaml_text,msg", [
    ("agency: A\nbriefs:\n  - {role: Electrical Estimator, location: Warrington, candidate_name: Jo Bloggs}\n",
     "personal data"),
    ("agency: A\nbriefs:\n  - {role: Electrical Estimator, location: Warrington, salary: 50000}\n", "personal data"),
    ("agency: A\nbriefs:\n  - {role: Electrical Estimator, location: Warrington, colour: red}\n", "unknown keys"),
    ("briefs:\n  - {role: Electrical Estimator, location: Warrington}\n", "needs 'agency'"),
    ("agency: A\nbriefs: []\n", "non-empty"),
])
def test_brief_file_rejects_personal_data_and_bad_shape(web, clock, tmp_path, yaml_text, msg):
    f = tmp_path / "b.yaml"
    f.write_text(yaml_text)
    with pytest.raises(BriefError, match=msg):
        import_briefs(services(web, clock), f)


def test_seed_sql_mirrors_python_seed():
    from app.role_seed import ROLE_FAMILIES, SEED_EMPLOYERS
    sql = (ROOT / "supabase" / "seed_role_match.sql").read_text(encoding="utf-8")
    for f in ROLE_FAMILIES:
        assert f"'{f['name']}'" in sql
        for t in f["exact_titles"] + f["related_titles"]:
            assert f"'{t}'" in sql
    for e in SEED_EMPLOYERS:
        assert e["careers_url"] in sql and e["name"] in sql


def test_no_personal_data_columns_in_role_match_schema():
    sql = (ROOT / "supabase" / "migrations" / "003_role_match.sql").read_text(encoding="utf-8").lower()
    for col in ("first_name", "last_name", "full_name", "email", "phone", "cv ", "candidate_name", "dob"):
        assert col not in sql


def test_six_brief_end_to_end_on_postgres(web, clock, store):
    from app.pg import PgStore  # noqa: F401
    t = web.transport
    svc = Services(repo=store, settings=Settings(reed_api_key=TEST_KEY, reed_requests_per_second=0),
                   fetcher=PoliteFetcher(transport=t, sleep=clock.sleep, clock=clock.monotonic),
                   geocoder=Geocoder(store, transport=t, sleep=clock.sleep, clock=clock.monotonic),
                   wayback=Wayback(store, transport=t, sleep=clock.sleep, clock=clock.monotonic))
    import_briefs(svc, ROOT / "briefs.example.yaml")
    # Postgres clock is real time; check with a wall clock that matches it.
    svc.clock = __import__("app.role_repo", fromlist=["utcnow"]).utcnow
    check_employers(svc)
    report = run_match(svc, AGENCY, use_reed=False)
    got = matched(report)
    assert ("Electrical Project Manager - Manchester", "DL M&E Building Services Ltd", "Electrical Project Manager") in got
    assert ("Electrical Estimator - Warrington", "Central Electrical & Mechanical NW Ltd", "Electrical Estimator") in got
    assert store.val("select count(*) from brief_matches") == len(report.rows)
