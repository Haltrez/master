"""Role-family title matching and Role Match scoring."""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field

from .careers import singular
from .normalise import title_norm

SENIORITY_WORDS = {"senior", "snr", "sr", "lead", "principal", "junior", "jnr", "jr", "trainee",
                   "graduate", "assistant", "chief", "head"}


def match_norm(title: str) -> str:
    """title_norm, but keeps bracketed words: in these families '(electrical)'
    vs '(mechanical)' is the whole difference."""
    t = re.sub(r"[()\[\]{}]", " ", title or "")
    toks = title_norm(t).split()
    return " ".join(singular(x) for x in toks)


def _core(tokens: list[str]) -> list[str]:
    return [t for t in tokens if t not in SENIORITY_WORDS]


@dataclass
class TitleMatch:
    kind: str                 # 'exact' | 'related'
    seniority_flag: str | None


DISCIPLINES = ("electrical", "mechanical")


def match_title(job_title: str, exact: list[str], related: list[str],
                family_name: str | None = None) -> TitleMatch | None:
    job = match_norm(job_title)
    toks = job.split()
    if not toks:
        return None
    seniority = next((t for t in toks if t in SENIORITY_WORDS), None)
    ex = [match_norm(x) for x in exact]
    rel = [match_norm(x) for x in related]
    # 1. equal titles  2. equal after dropping seniority words  3. every word of
    # a family title appears in the ad title. Exact beats related at each step.
    # The loose word-subset step must not cross disciplines: a "Mechanical
    # Pre-Construction Manager" ad is not related to the Electrical family
    # just because "pre-construction manager" is in its related list.
    fam_disc = {d for d in DISCIPLINES if d in (family_name or "").lower()}
    ad_disc = {d for d in DISCIPLINES if d in toks}
    crosses = bool(fam_disc and ad_disc and not (fam_disc & ad_disc))
    steps = (
        lambda fam: job == fam,
        lambda fam: _core(toks) == _core(fam.split()),
        lambda fam: bool(fam) and not crosses and set(fam.split()) <= set(toks),
    )
    for step in steps:
        if any(step(f) for f in ex):
            return TitleMatch("exact", seniority)
        if any(step(f) for f in rel):
            return TitleMatch("related", seniority)
    return None


@dataclass
class ScoreInput:
    title_match: TitleMatch
    distance_miles: float | None
    radius_miles: int
    open_since: dt.date | None          # earliest of our first_seen, Wayback, employer's date_posted
    first_seen: dt.date
    last_seen: dt.date
    reposted: bool
    family_roles_at_employer: int
    sector_keywords_found: list[str]
    today: dt.date
    brief_seniority: str | None = None
    active: bool = True                 # a new posting / new job URL / changed page in the last 30 days
    evergreen_reason: str | None = None


@dataclass
class Score:
    points: int
    reasons: list[str] = field(default_factory=list)
    in_range: bool = True


def score(s: ScoreInput) -> Score:
    pts = 0
    reasons: list[str] = []
    if s.title_match.kind == "exact":
        pts += 4
        reasons.append("Exact title match (+4)")
    else:
        pts += 2
        reasons.append("Related title (+2)")
    flag = s.title_match.seniority_flag
    if flag and (s.brief_seniority or "").lower() != flag:
        reasons.append(f"Note: ad is for a {flag} role")

    in_range = True
    if s.distance_miles is None:
        reasons.append("Distance unknown")
    elif s.distance_miles <= s.radius_miles:
        pts += 2
        reasons.append(f"{s.distance_miles:.0f} miles away, within {s.radius_miles} (+2)")
    elif s.distance_miles <= s.radius_miles * 1.5:
        pts += 1
        reasons.append(f"{s.distance_miles:.0f} miles away, just outside {s.radius_miles} (+1)")
    else:
        in_range = False
        reasons.append(f"{s.distance_miles:.0f} miles away, too far")

    # Client feedback: a long-open page is often an evergreen listing, not demand. Being open a long time
    # scores nothing; being advertised again does.
    if s.reposted:
        pts += 4
        reasons.append("Re-advertised in the last 30 days (+4)")
    if s.evergreen_reason:
        reasons.append(f"Possibly evergreen: {s.evergreen_reason} (+0)")
    if not s.active:
        in_range = False
        reasons.append("Watch list: no new posting, new link or page change in the last 30 days")
    if s.family_roles_at_employer >= 2:
        pts += 1
        reasons.append(f"{s.family_roles_at_employer} roles in this family open at once (+1)")
    if s.sector_keywords_found:
        pts += 1
        reasons.append("Sector: " + ", ".join(s.sector_keywords_found) + " (+1)")
    if (s.open_since and (s.today - s.open_since).days > 60 and (s.today - s.last_seen).days > 14):
        pts -= 3
        reasons.append("Older than 60 days and not seen for 14 days, probably filled (-3)")
    return Score(pts, reasons, in_range)
