"""Known applicant-tracking systems with documented, public, keyless job-board APIs.

Docs read before building (Nov 2026):
- Greenhouse Job Board API: https://developers.greenhouse.io/job-board.html
  GET https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs  (no auth for GET)
- Lever Postings API: https://github.com/lever/postings-api
  GET https://api.lever.co/v0/postings/{site}?mode=json  (EU: api.eu.lever.co)
- Ashby Public Job Posting API: https://developers.ashbyhq.com/docs/public-job-posting-api
  GET https://api.ashbyhq.com/posting-api/job-board/{job_board_name}

Not supported as an API: Breathe HR (its API needs the company's own key), so
Breathe-hosted vacancies are read from the employer's careers page and the
public /v/ vacancy pages that Breathe's robots.txt allows.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from .careers import ExtractedJob, clean_title


@dataclass(frozen=True)
class AtsBoard:
    ats_type: str
    api_url: str
    board_url: str


def detect_ats(url: str | None) -> AtsBoard | None:
    if not url:
        return None
    u = urlsplit(url)
    host = (u.hostname or "").lower()
    seg = [s for s in u.path.split("/") if s]
    if not seg:
        return None
    token = seg[0]
    if host in ("boards.greenhouse.io", "job-boards.greenhouse.io", "job-boards.eu.greenhouse.io"):
        if token == "embed" and "for=" in u.query:
            token = u.query.split("for=", 1)[1].split("&", 1)[0]
        return AtsBoard("greenhouse", f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs",
                        f"https://{host}/{token}")
    if host == "jobs.lever.co":
        return AtsBoard("lever", f"https://api.lever.co/v0/postings/{token}?mode=json", f"https://{host}/{token}")
    if host == "jobs.eu.lever.co":
        return AtsBoard("lever", f"https://api.eu.lever.co/v0/postings/{token}?mode=json", f"https://{host}/{token}")
    if host == "jobs.ashbyhq.com":
        return AtsBoard("ashby", f"https://api.ashbyhq.com/posting-api/job-board/{token}", f"https://{host}/{token}")
    return None


def parse_ats(ats_type: str, body: Any) -> list[ExtractedJob]:
    jobs: list[ExtractedJob] = []
    if ats_type == "greenhouse":
        for j in (body or {}).get("jobs", []):
            jobs.append(ExtractedJob(clean_title(j.get("title", ""))[0], (j.get("location") or {}).get("name"),
                                     j.get("absolute_url"), date_posted=(j.get("first_published") or None) and
                                     str(j["first_published"])[:10]))
    elif ats_type == "lever":
        for j in body or []:
            cats = j.get("categories") or {}
            created = j.get("createdAt")
            posted = None
            if isinstance(created, (int, float)):
                import datetime as dt
                posted = dt.datetime.fromtimestamp(created / 1000, dt.timezone.utc).date().isoformat()
            jobs.append(ExtractedJob(clean_title(j.get("text", ""))[0], cats.get("location"),
                                     j.get("hostedUrl"), date_posted=posted))
    elif ats_type == "ashby":
        for j in (body or {}).get("jobs", []):
            if j.get("isListed") is False:
                continue
            jobs.append(ExtractedJob(clean_title(j.get("title", ""))[0], j.get("location"), j.get("jobUrl"),
                                     date_posted=(j.get("publishedAt") or None) and str(j["publishedAt"])[:10]))
    return [j for j in jobs if j.title]
