import datetime as dt

import pytest

from app.role_seed import ROLE_FAMILIES
from app.roles import ScoreInput, TitleMatch, match_title, score

FAM = {f["name"]: f for f in ROLE_FAMILIES}


def m(title, family):
    f = FAM[family]
    return match_title(title, f["exact_titles"], f["related_titles"], f["name"])


@pytest.mark.parametrize("title,family,kind,flag", [
    ("Electrical Estimator", "Electrical Estimator", "exact", None),
    ("Senior Electrical Estimator", "Electrical Estimator", "exact", "senior"),
    ("Estimator (Electrical)", "Electrical Estimator", "exact", None),
    ("M&E Estimator", "Electrical Estimator", "exact", None),
    ("Mechanical Estimator", "Electrical Estimator", "related", None),
    ("Senior Mechanical Estimator", "Electrical Estimator", "related", "senior"),
    ("Electrical Project Manager", "Electrical Project Manager", "exact", None),
    ("Trainee Electrical Project Manager", "Electrical Project Manager", "related", "trainee"),
    ("M&E Supervisors and Site Managers", "Electrical Site Manager", "exact", None),
    ("Electrical Site Supervisor", "Electrical Site Manager", "exact", None),
    ("Electrical Supervisors", "Electrical Site Manager", "related", None),
    ("Pre-Construction Manager (Mechanical)", "Mechanical Pre-Construction Manager", "exact", None),
    ("Pre-Construction Manager", "Electrical Pre-Construction Manager", "related", None),
    ("HVAC Design Engineer", "Mechanical Design Engineer", "exact", None),
])
def test_matches(title, family, kind, flag):
    r = m(title, family)
    assert r is not None and (r.kind, r.seniority_flag) == (kind, flag)


@pytest.mark.parametrize("title,family", [
    ("Estimator", "Electrical Estimator"),                          # too vague
    ("Mechanical Pre-Construction Manager", "Electrical Pre-Construction Manager"),  # other discipline
    ("Electrical Engineers", "Electrical Project Manager"),
    ("Senior Gas Engineer", "Mechanical Design Engineer"),
    ("Electrical Testing & Inspection Engineer", "Electrical Project Manager"),
])
def test_non_matches(title, family):
    assert m(title, family) is None


TODAY = dt.date(2026, 11, 4)


def si(**kw):
    base = dict(title_match=TitleMatch("exact", None), distance_miles=10, radius_miles=30, open_since=None,
                first_seen=TODAY, last_seen=TODAY, reposted=False, family_roles_at_employer=1,
                sector_keywords_found=[], today=TODAY)
    base.update(kw)
    return ScoreInput(**base)


def test_score_each_signal():
    assert score(si()).points == 4 + 2
    assert score(si(title_match=TitleMatch("related", None))).points == 2 + 2
    assert score(si(distance_miles=40)).points == 4 + 1            # within 1.5x radius
    far = score(si(distance_miles=46))
    assert not far.in_range
    assert score(si(distance_miles=None)).points == 4              # unknown distance: kept, ranked lower
    assert score(si(open_since=TODAY - dt.timedelta(days=30))).points == 6       # open a long time: no points
    assert score(si(reposted=True)).points == 6 + 4                                # re-advertised in 30 days: +4
    assert score(si(family_roles_at_employer=2)).points == 6 + 1
    assert score(si(sector_keywords_found=["m&e"])).points == 6 + 1
    filled = score(si(open_since=TODAY - dt.timedelta(days=61), last_seen=TODAY - dt.timedelta(days=15)))
    assert filled.points == 6 - 3 and any("probably filled" in r for r in filled.reasons)
    ever = score(si(active=False, evergreen_reason="page unchanged since 1 Aug 2026"))
    assert not ever.in_range and any(r.startswith("Possibly evergreen") for r in ever.reasons)   # watch list


def test_score_reasons_are_plain_english_and_flag_seniority():
    r = score(si(title_match=TitleMatch("exact", "senior")))
    assert r.reasons[0] == "Exact title match (+4)"
    assert "Note: ad is for a senior role" in r.reasons
    same = score(si(title_match=TitleMatch("exact", "senior"), brief_seniority="senior"))
    assert "Note: ad is for a senior role" not in same.reasons
