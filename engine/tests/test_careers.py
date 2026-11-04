from pathlib import Path

import pytest

from app.careers import clean_title, extract, looks_like_job_title
from app.role_seed import DEFAULT_SECTOR_KEYWORDS

FIX = Path(__file__).parent / "fixtures"


def page(name):
    return (FIX / f"careers_{name}.html").read_text(encoding="utf-8")


def titles(ex):
    return [j.title for j in ex.jobs]


def test_dl_job_board():
    ex = extract(page("dl"), "https://www.dl-bsl.co.uk/job-board", DEFAULT_SECTOR_KEYWORDS)
    t = titles(ex)
    assert "Electrical Project Manager" in t and "Trainee Electrical Project Manager" in t
    assert "M&E Supervisors and Site Managers" in t
    # "We are always on the lookout for: Project Managers, Site Operatives..." is not a vacancy list
    assert "Project Managers" not in t and "Designers" not in t
    epm = next(j for j in ex.jobs if j.title == "Electrical Project Manager")
    assert epm.url == "https://www.dl-bsl.co.uk/jobs/electrical-project-manager"
    cardiff = next(j for j in ex.jobs if j.title == "Electrician")
    assert cardiff.location_text == "Cardiff"
    assert "m&e" in epm.keywords_found
    assert len(ex.page_hash) == 64 and not ex.js_only


def test_ce_plain_text_titles_with_docx_links():
    ex = extract(page("ce"), "https://celectrical.com/careers/")
    assert titles(ex) == ["Electrical Labourers", "Electrician’s Mates", "Electrical Site Supervisor",
                          "Electrical Engineers", "Electrical Estimator", "Mechanical Estimator"]
    assert ex.jobs[4].url.endswith("Electrical-Estimator.docx")


def test_be_shared_links_and_no_person_names():
    ex = extract(page("be"), "https://b-enggroup.co.uk/jobs/")
    t = titles(ex)
    assert "Senior Mechanical Estimator" in t
    assert not any("Alex" in x or "Example" in x for x in t)  # a named person on the page (name replaced in fixture)
    urls = [j.url for j in ex.jobs]
    assert len(urls) == len(set(urls))  # two roles sharing one link get distinct URLs
    assert "https://b-enggroup.co.uk/jobs/#service-coordinator" in urls


def test_no_emails_or_page_text_in_extracted_jobs():
    for name, base in (("dl", "https://www.dl-bsl.co.uk/job-board"), ("ce", "https://celectrical.com/careers/"),
                       ("be", "https://b-enggroup.co.uk/jobs/")):
        for j in extract(page(name), base).jobs:
            assert "@" not in j.title and "@" not in (j.location_text or "")
            assert len(j.title) < 90


def test_js_only_page_detected():
    html = '<html><body><div id="root"></div><script src="/app.js"></script></body></html>'
    ex = extract(html, "https://acme.co.uk/careers")
    assert ex.jobs == [] and ex.js_only


def test_jsonld_jobposting_preferred():
    html = """<html><body><h2>Electrical Estimator</h2>
    <script type="application/ld+json">{"@context":"https://schema.org","@graph":[{"@type":"JobPosting",
    "title":"Senior Electrical Estimator","url":"/jobs/1","datePosted":"2026-09-01T00:00:00",
    "jobLocation":{"@type":"Place","address":{"addressLocality":"Warrington","postalCode":"WA1 1AA"}}}]}</script>
    </body></html>"""
    ex = extract(html, "https://acme.co.uk/careers")
    assert len(ex.jobs) == 1
    j = ex.jobs[0]
    assert (j.title, j.location_text, j.url, j.date_posted) == (
        "Senior Electrical Estimator", "Warrington, WA1 1AA", "https://acme.co.uk/jobs/1", "2026-09-01")


@pytest.mark.parametrize("raw,title,loc", [
    ("AP/Electrical Testing & Inspection Engineer - Manchester", "AP/Electrical Testing & Inspection Engineer", "Manchester"),
    ("Electrician – Manchester City Centre", "Electrician", "Manchester City Centre"),
    ("Electrical Estimator Job", "Electrical Estimator", None),
    ("Pre-Construction Manager (Mechanical)", "Pre-Construction Manager (Mechanical)", None),
    ("Electrical Project Manager (Warrington)", "Electrical Project Manager", "Warrington"),
    ("Credit Controller (Maternity Cover)", "Credit Controller (Maternity Cover)", None),
    ("Estimator - Electrical", "Estimator - Electrical", None),
])
def test_clean_title(raw, title, loc):
    assert clean_title(raw) == (title, loc)


@pytest.mark.parametrize("text,ok", [
    ("Electrical Estimator", True), ("Senior Gas Engineer", True), ("Estimator (Electrical)", True),
    ("Karl Killen, Contracts Manager", False), ("Meet Our Team", False), ("Electrical Services", False),
    ("Join our professional team", False), ("What is an EICR? 9 Essential Areas To Know", False),
    ("Please apply via email to someone@example.com", False), ("M&E Project Management", False),
])
def test_looks_like_job_title(text, ok):
    assert looks_like_job_title(text) is ok
