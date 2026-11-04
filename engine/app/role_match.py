"""Role Match: find direct employers hiring the roles an agency's candidates do.

A candidate brief is a role profile only (role, location, radius, sector
keywords, seniority). Never names, CVs, contact details or individual salaries.
"""

from __future__ import annotations

import csv
import datetime as dt
import logging
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from .ats import detect_ats, parse_ats
from .careers import (ExtractedJob, detail_extract, display_url, extract, find_keywords, is_document_link,
                      is_synthetic_url, page_text_hash)
from .collect import agency_name_pattern, classify
from .config import Settings
from .geo import Geocoder, miles_between
from .normalise import employer_norm, lead_key, location_norm, title_norm
from .polite import FetchRefused, ForbiddenHost, PageBudget, PoliteFetcher, RobotsDisallowed
from .reed_client import CallBudget, ReedClient, ReedError
from .role_repo import RoleRepo, parse_ts, utcnow
from .role_seed import DEFAULT_SECTOR_KEYWORDS
from .roles import Score, ScoreInput, TitleMatch, match_title, score
from .wayback import Wayback

log = logging.getLogger("role_match")

BRIEF_KEYS = {"role", "location", "radius", "sector_keywords", "seniority", "label"}
TOP_KEYS = {"agency", "sector_keywords", "briefs"}
PERSONAL_KEYS = {"name", "candidate", "candidate_name", "first_name", "last_name", "full_name", "cv", "email",
                 "phone", "mobile", "salary", "address", "linkedin", "dob"}

CSV_COLUMNS = ["candidate_role", "company", "their_open_role", "location", "distance_miles", "open_since",
               "signal", "link", "source", "score"]
SOURCE_LABELS = {"careers_page": "Company careers page", "ats": "Company job board", "reed": "Reed (direct employer)"}


class BriefError(ValueError):
    pass


@dataclass
class Services:
    repo: RoleRepo
    settings: Settings
    fetcher: PoliteFetcher | None = None
    geocoder: Geocoder | None = None
    wayback: Wayback | None = None
    reed: ReedClient | None = None
    clock: Any = utcnow

    def today(self) -> dt.date:
        return self.clock().date()


# ---------------------------------------------------------------------------
# Briefs
# ---------------------------------------------------------------------------

def find_family(repo: RoleRepo, role: str) -> dict:
    fams = repo.families()
    for f in fams:
        if f["name"].lower() == role.strip().lower():
            return f
    raise BriefError(f"unknown role '{role}'. Known roles: " + "; ".join(f["name"] for f in fams))


def add_brief(svc: Services, agency: str, role: str, location: str, radius: int = 30,
              sector_keywords: list[str] | None = None, seniority: str | None = None,
              label: str | None = None) -> dict:
    fam = find_family(svc.repo, role)
    ag = svc.repo.agency_by_name(agency) or svc.repo.create_agency(agency)
    place = svc.geocoder.geocode(location) if svc.geocoder else None
    if place is None:
        log.warning("could not geocode brief location '%s'; distances will be unknown", location)
    row = {
        "agency_id": ag["id"], "role_family_id": fam["id"], "label": label or f"{fam['name']} - {location}",
        "location": location, "lat": place.lat if place else None, "lng": place.lng if place else None,
        "radius_miles": int(radius), "sector_keywords": list(sector_keywords or []),
        "seniority": seniority, "active": True,
    }
    return svc.repo.save_brief(row)


def _check_keys(d: dict, allowed: set[str], where: str) -> None:
    bad = {k for k in d if str(k).lower() in PERSONAL_KEYS}
    if bad:
        raise BriefError(f"{where}: {sorted(bad)} look like candidate personal data. Briefs hold the role "
                         "profile only: role, location, radius, sector_keywords, seniority.")
    unknown = set(d) - allowed
    if unknown:
        raise BriefError(f"{where}: unknown keys {sorted(unknown)}; allowed: {sorted(allowed)}")


def load_brief_file(path: Path) -> dict:
    import yaml

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise BriefError("briefs file must be a mapping with agency and briefs")
    _check_keys(data, TOP_KEYS, "top level")
    if not data.get("agency"):
        raise BriefError("briefs file needs 'agency'")
    briefs = data.get("briefs") or []
    if not isinstance(briefs, list) or not briefs:
        raise BriefError("briefs file needs a non-empty 'briefs' list")
    for i, b in enumerate(briefs, 1):
        if not isinstance(b, dict):
            raise BriefError(f"brief {i} must be a mapping")
        _check_keys(b, BRIEF_KEYS, f"brief {i}")
        if not b.get("role") or not b.get("location"):
            raise BriefError(f"brief {i} needs role and location")
    return data


def import_briefs(svc: Services, path: Path) -> list[dict]:
    data = load_brief_file(path)
    shared = [str(k) for k in data.get("sector_keywords") or []]
    out = []
    for b in data["briefs"]:
        kws = [str(k) for k in b.get("sector_keywords") or shared]
        out.append(add_brief(svc, str(data["agency"]), str(b["role"]), str(b["location"]),
                             int(b.get("radius") or 30), kws, b.get("seniority"), b.get("label")))
    return out


# ---------------------------------------------------------------------------
# Employer checks (careers pages + ATS boards)
# ---------------------------------------------------------------------------

@dataclass
class CheckResult:
    employer_id: int
    name: str
    outcome: str                  # checked | skipped | blocked_by_robots | needs_manual | error
    jobs: int = 0
    pages: int = 0
    note: str = ""


def _family_titles(repo: RoleRepo) -> list[tuple[str, list[str], list[str]]]:
    return [(f["name"], f["exact_titles"], f["related_titles"]) for f in repo.families()]


def _matches_any_family(title: str, fams: list[tuple[str, list[str], list[str]]]) -> bool:
    return any(match_title(title, ex, rel, name) for name, ex, rel in fams)


def is_due(emp: dict, now: dt.datetime, force: bool = False) -> tuple[bool, str]:
    last = parse_ts(emp.get("last_checked_at"))
    if last is None:
        return True, ""
    age = now - last
    if age < dt.timedelta(days=1):
        return False, "already checked in the last 24 hours (max once a day)"
    if not force and age < dt.timedelta(days=max(1, int(emp.get("check_every_days") or 7))):
        return False, f"not due (checked {age.days} days ago, every {emp.get('check_every_days') or 7})"
    return True, ""


def check_employer(svc: Services, emp: dict, force: bool = False, extra_keywords: list[str] | None = None) -> CheckResult:
    assert svc.fetcher is not None
    res = CheckResult(emp["id"], emp["name"], "skipped")
    if emp.get("status") == "ignore":
        res.note = "status is ignore"
        return res
    url = emp.get("careers_url")
    if not url:
        res.note = "no careers URL yet (research queue)"
        return res
    now = svc.clock()
    due, why = is_due(emp, now, force)
    if not due:
        res.note = why
        return res

    keywords = sorted({k.lower() for k in DEFAULT_SECTOR_KEYWORDS + (extra_keywords or [])})
    fams = _family_titles(svc.repo)
    budget = PageBudget()
    status = "active"
    source = "careers_page"
    ats_type = emp.get("ats_type") or "html"
    jobs: list[ExtractedJob] = []
    page_hash = None
    try:
        board = detect_ats(url)
        if board:
            resp = svc.fetcher.get(board.api_url, budget)
            resp.raise_for_status()
            jobs, source, ats_type, page_hash = parse_ats(board.ats_type, resp.json()), "ats", board.ats_type, \
                page_text_hash(resp.text)
        else:
            resp = svc.fetcher.get(url, budget)
            if resp.status_code >= 400:
                raise httpx.HTTPStatusError(f"careers page returned {resp.status_code}", request=resp.request,
                                            response=resp)
            ex = extract(resp.text, str(resp.url), keywords)
            jobs, page_hash, ats_type = ex.jobs, ex.page_hash, "html"
            if not jobs and ex.ats_links:
                board = detect_ats(ex.ats_links[0])
                if board:
                    r2 = svc.fetcher.get(board.api_url, budget)
                    r2.raise_for_status()
                    jobs, source, ats_type = parse_ats(board.ats_type, r2.json()), "ats", board.ats_type
            if not jobs and ex.js_only:
                status = "needs_manual"
                res.note = "careers page needs JavaScript; check it by hand"
            # Up to 2 job detail pages (3-page cap), role-family matches first.
            details = [j for j in jobs if j.url and not is_synthetic_url(j.url) and not is_document_link(j.url)
                       and display_url(j.url) != display_url(url)]
            details.sort(key=lambda j: not _matches_any_family(j.title, fams))
            for j in details[:2]:
                try:
                    d = svc.fetcher.get(j.url, budget)
                except RobotsDisallowed:
                    continue
                except FetchRefused:
                    break
                except httpx.HTTPError:
                    continue
                if d.status_code < 400 and "html" in d.headers.get("content-type", "html"):
                    loc, posted, kws = detail_extract(d.text, str(d.url), keywords)
                    j.location_text = j.location_text or loc
                    j.date_posted = j.date_posted or posted
                    j.keywords_found = sorted(set(j.keywords_found) | set(kws))
    except RobotsDisallowed as exc:
        status, res.outcome, res.note = "blocked_by_robots", "blocked_by_robots", str(exc)
    except ForbiddenHost as exc:
        status, res.outcome, res.note = "needs_manual", "needs_manual", str(exc)
    except (httpx.HTTPError, ValueError) as exc:
        res.outcome, res.note = "error", f"{type(exc).__name__}: {exc}"
        svc.repo.update_employer(emp["id"], {"last_checked_at": now})
        res.pages = budget.used
        return res

    for j in jobs:
        place = svc.geocoder.geocode(j.location_text) if (svc.geocoder and j.location_text) else None
        svc.repo.upsert_job({
            "employer_id": emp["id"], "source": source, "title": j.title[:200],
            "title_norm": title_norm(j.title), "location_text": j.location_text,
            "lat": place.lat if place else None, "lng": place.lng if place else None,
            "url": j.url or display_url(url), "page_hash": page_hash,
            "date_posted": j.date_posted, "keywords_found": j.keywords_found,
        })
    fields: dict[str, Any] = {"last_checked_at": now, "status": status, "ats_type": ats_type}
    if emp.get("lat") is None and emp.get("town") and svc.geocoder:
        p = svc.geocoder.geocode(emp["town"])
        if p:
            fields.update(lat=p.lat, lng=p.lng)
    svc.repo.update_employer(emp["id"], fields)
    if res.outcome == "skipped":
        res.outcome = "needs_manual" if status == "needs_manual" else "checked"
    res.jobs, res.pages = len(jobs), budget.used
    log.info("employer %d '%s': %s, %d jobs, %d pages %s", emp["id"], emp["name"], res.outcome, res.jobs,
             res.pages, res.note)
    return res


def check_employers(svc: Services, ids: list[int] | None = None, force: bool = False,
                    extra_keywords: list[str] | None = None) -> list[CheckResult]:
    out = []
    for emp in svc.repo.employers():
        if ids is not None and emp["id"] not in ids:
            continue
        if ids is None and not emp.get("careers_url"):
            continue
        out.append(check_employer(svc, emp, force, extra_keywords))
        svc.repo.save()
    return out


# ---------------------------------------------------------------------------
# Reed: direct-employer discovery + agency "market demand"
# ---------------------------------------------------------------------------

@dataclass
class Demand:
    count: int | None
    radius: int
    note: str = ""


def reed_keywords(fam: dict) -> list[str]:
    seen, out = set(), []
    for t in fam["exact_titles"]:
        kw = " ".join(t.replace("(", " ").replace(")", " ").split())
        if kw.lower() not in seen:
            seen.add(kw.lower())
            out.append(kw)
    return out


def reed_discover(svc: Services, briefs: list[dict], fams: dict[int, dict]) -> dict[int, Demand]:
    demand: dict[int, Demand] = {}
    if svc.reed is None:
        for b in briefs:
            demand[b["id"]] = Demand(None, b["radius_miles"], "REED_API_KEY not set")
        return demand
    budget = CallBudget(svc.settings.reed_max_calls_per_run)
    pattern = agency_name_pattern(svc.settings.agency_name_words)
    for b in briefs:
        fam = fams[b["role_family_id"]]
        radius = math.ceil(b["radius_miles"] * 1.5)
        agency_ids: set[int] = set()
        try:
            for kw in reed_keywords(fam):
                for queried_as in ("employer", "agency"):
                    for job in svc.reed.search(kw, b["location"], radius, None, queried_as, budget=budget):
                        if not match_title(job.job_title, fam["exact_titles"], fam["related_titles"], fam["name"]):
                            continue
                        posted_by, _ = classify(job, queried_as, {}, pattern)
                        if posted_by == "agency":
                            agency_ids.add(job.job_id)
                            continue
                        emp = svc.repo.add_employer({"name": job.employer_name, "source": "reed",
                                                     "status": "research", "town": job.location_name})
                        place = svc.geocoder.geocode(job.location_name) if svc.geocoder else None
                        svc.repo.upsert_job({
                            "employer_id": emp["id"], "source": "reed", "title": job.job_title,
                            "title_norm": title_norm(job.job_title, job.location_name),
                            "location_text": job.location_name,
                            "lat": place.lat if place else None, "lng": place.lng if place else None,
                            "url": job.job_url or f"https://www.reed.co.uk/jobs/{job.job_id}",
                            "date_posted": job.posted_date.isoformat() if job.posted_date else None,
                            "keywords_found": find_keywords(job.job_description or "", DEFAULT_SECTOR_KEYWORDS),
                        })
            demand[b["id"]] = Demand(len(agency_ids), radius)
        except ReedError as exc:
            log.warning("reed: %s", exc)
            demand[b["id"]] = Demand(len(agency_ids) if agency_ids else None, radius, f"Reed incomplete: {exc}")
    return demand


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------

@dataclass
class MatchRow:
    brief: dict
    family: dict
    job: dict
    employer: dict
    title_match: TitleMatch
    distance: float | None
    location_label: str
    located_by_base: bool
    result: Score | None = None
    open_since: dt.date | None = None
    open_since_label: str = "unknown"
    family_roles: int = 0


@dataclass
class MatchReport:
    agency: dict
    rows: list[MatchRow] = field(default_factory=list)
    demand: dict[int, Demand] = field(default_factory=dict)
    briefs: list[dict] = field(default_factory=list)
    regions: dict[int, str | None] = field(default_factory=dict)


def _is_current(job: dict, emp: dict) -> bool:
    """Careers/ATS jobs must have been on the page at the employer's latest check."""
    if job["source"] == "reed":
        return True
    last_check = parse_ts(emp.get("last_checked_at"))
    seen = parse_ts(job["last_seen_at"])
    return last_check is None or seen >= last_check - dt.timedelta(hours=1)


def _date(v: Any) -> dt.date | None:
    if not v:
        return None
    if isinstance(v, dt.date) and not isinstance(v, dt.datetime):
        return v
    return parse_ts(v).date() if "T" in str(v) else dt.date.fromisoformat(str(v)[:10])


def _month(d: dt.date) -> str:
    return "Sept" if d.month == 9 else f"{d:%b}"


def run_match(svc: Services, agency_name: str, use_reed: bool = True) -> MatchReport:
    repo = svc.repo
    agency = repo.agency_by_name(agency_name)
    if agency is None:
        raise BriefError(f"no agency called '{agency_name}'. Add briefs first: python cli.py brief add ...")
    briefs = [b for b in repo.briefs(agency["id"]) if b.get("active", True)]
    if not briefs:
        raise BriefError(f"'{agency['name']}' has no active briefs")
    fams = {f["id"]: f for f in repo.families()}
    report = MatchReport(agency=agency, briefs=briefs)
    report.demand = reed_discover(svc, briefs, fams) if use_reed else {
        b["id"]: Demand(None, b["radius_miles"], "Reed skipped") for b in briefs}

    employers = {e["id"]: e for e in repo.employers()}
    jobs = [j for j in repo.jobs() if j["employer_id"] in employers
            and employers[j["employer_id"]].get("status") != "ignore"
            and _is_current(j, employers[j["employer_id"]])]
    today = svc.today()

    candidates: list[MatchRow] = []
    for b in briefs:
        fam = fams[b["role_family_id"]]
        here = (float(b["lat"]), float(b["lng"])) if b.get("lat") is not None else None
        place = svc.geocoder.geocode(b["location"]) if svc.geocoder else None
        report.regions[b["id"]] = place.region if place else None
        fam_hits = {}
        for j in jobs:
            tm = match_title(j["title"], fam["exact_titles"], fam["related_titles"], fam["name"])
            if tm:
                fam_hits[j["id"]] = tm
        per_emp: dict[int, int] = {}
        for jid in fam_hits:
            eid = next(x["employer_id"] for x in jobs if x["id"] == jid)
            per_emp[eid] = per_emp.get(eid, 0) + 1
        for j in jobs:
            tm = fam_hits.get(j["id"])
            if not tm:
                continue
            emp = employers[j["employer_id"]]
            by_base = False
            if j.get("lat") is not None:
                there, label = (float(j["lat"]), float(j["lng"])), j.get("location_text") or ""
            elif emp.get("lat") is not None:
                there, label, by_base = (float(emp["lat"]), float(emp["lng"])), f"{emp.get('town') or ''} (company base)", True
            else:
                there, label = None, j.get("location_text") or emp.get("town") or ""
            dist = miles_between(here, there) if (here and there) else None
            candidates.append(MatchRow(b, fam, j, emp, tm, dist, label, by_base, family_roles=per_emp[emp["id"]]))

    # Wayback only for matched jobs (and their careers pages), throttled + cached.
    hot = repo.hot_lead_keys(sorted({
        lead_key(employer_norm(m.employer["name"]), m.job["title_norm"],
                 location_norm(m.job.get("location_text") or m.employer.get("town"))) for m in candidates}))
    for m in candidates:
        j, emp = m.job, m.employer
        wb = _date(j.get("wayback_first_seen"))
        if wb is None and svc.wayback and not is_synthetic_url(j["url"]):
            wb = svc.wayback.first_capture(j["url"])
            if wb:
                repo.update_job(j["id"], {"wayback_first_seen": wb})
                j["wayback_first_seen"] = wb.isoformat()
        evidence = [d for d in (wb, _date(j.get("date_posted"))) if d]
        first_seen = _date(j["first_seen_at"])
        if evidence:
            m.open_since = min(evidence + [first_seen])
            m.open_since_label = f"{_month(m.open_since)} {m.open_since:%Y}"
        elif (today - first_seen).days >= 7:
            m.open_since = first_seen
            m.open_since_label = f"by {first_seen:%-d %b %Y} (when we first saw it)"
        else:
            careers_cap = svc.wayback.first_capture(emp.get("careers_url")) if (svc.wayback and emp.get("careers_url")) else None
            m.open_since_label = (f"unknown (careers page captured since {_month(careers_cap)} {careers_cap:%Y})"
                                  if careers_cap else "unknown")
        kws = {k.lower() for k in (m.brief.get("sector_keywords") or [])} or {k.lower() for k in DEFAULT_SECTOR_KEYWORDS}
        found = sorted({k for k in (x.lower() for x in j.get("keywords_found") or []) if k in kws}
                       | {k.lower() for k in find_keywords(j["title"], sorted(kws))})
        key = lead_key(employer_norm(emp["name"]), j["title_norm"], location_norm(j.get("location_text") or emp.get("town")))
        m.result = score(ScoreInput(
            title_match=m.title_match, distance_miles=m.distance, radius_miles=int(m.brief["radius_miles"]),
            open_since=m.open_since, first_seen=first_seen, last_seen=_date(j["last_seen_at"]),
            reposted=key in hot, family_roles_at_employer=m.family_roles, sector_keywords_found=found,
            today=today, brief_seniority=m.brief.get("seniority")))
        if m.located_by_base:
            m.result.reasons.append(f"Location: company base ({emp.get('town')}), the ad doesn't say")
        if m.result.in_range:
            report.rows.append(m)
            repo.upsert_match({"brief_id": m.brief["id"], "employer_job_id": j["id"], "score": m.result.points,
                               "reasons": m.result.reasons,
                               "distance_miles": round(m.distance, 1) if m.distance is not None else None,
                               "signal": signal(m), "open_since_label": m.open_since_label,
                               "location_label": m.location_label})
    for b in briefs:
        d = report.demand.get(b["id"])
        repo.cache_set(f"demand:{b['id']}", {"sentence": demand_sentence(b, d, report.regions.get(b["id"]))})
    report.rows.sort(key=lambda r: (r.brief["label"].lower(), -r.result.points,
                                    r.distance if r.distance is not None else 1e9))
    return report


def signal(m: MatchRow) -> str:
    parts = []
    if m.open_since and m.result and any("30+ days" in r for r in m.result.reasons):
        parts.append(f"Open since {_month(m.open_since)}")
    if m.result and any(r.startswith("Reposted") for r in m.result.reasons):
        parts.append("reposted on Reed")
    if m.family_roles >= 2:
        parts.append(f"{m.family_roles} {m.family['name']} roles open at once")
    if m.title_match.kind == "related":
        parts.append("related role")
    if m.title_match.seniority_flag and (m.brief.get("seniority") or "").lower() != m.title_match.seniority_flag:
        parts.append(f"{m.title_match.seniority_flag} level")
    if m.result and any("probably filled" in r for r in m.result.reasons):
        parts.append("may already be filled")
    if not parts:
        parts.append("Live on their careers page" if m.job["source"] != "reed" else "Live on Reed")
    s = ", ".join(parts)
    return s[0].upper() + s[1:]


def demand_sentence(b: dict, d: Demand | None, region: str | None) -> str:
    where = f"within {d.radius if d else b['radius_miles']} miles of {b['location']}" + (f" ({region})" if region else "")
    if d is None or d.count is None:
        return f"Market demand: not checked ({d.note if d and d.note else 'Reed skipped'})"
    return f"Market demand: {d.count} agency ad{'s' if d.count != 1 else ''} for this role {where} right now"


def write_csv(report: MatchReport, path: Path) -> int:
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(CSV_COLUMNS)
        for m in report.rows:
            w.writerow([
                m.brief["label"], m.employer["name"], m.job["title"], m.location_label,
                f"{m.distance:.0f}" if m.distance is not None else "unknown",
                m.open_since_label, signal(m), display_url(m.job["url"]),
                SOURCE_LABELS.get(m.job["source"], m.job["source"]), m.result.points if m.result else "",
            ])
        w.writerow([])
        for b in sorted(report.briefs, key=lambda x: x["label"].lower()):
            w.writerow([b["label"], "", "", "", "", "",
                        demand_sentence(b, report.demand.get(b["id"]), report.regions.get(b["id"])), "", "", ""])
    return len(report.rows)
