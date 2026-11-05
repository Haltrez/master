"""Tools for the project agent: everything needed to research a lead-list brief by hand, done by code.

Each tool returns small JSON-able dicts (the agent's context is precious) and never raises for an
expected failure: it returns {"error": "..."} so the agent can decide what to do.

Rules built in, whatever the brief says:
- employers' own sites only through PoliteFetcher (robots.txt, 1 request / 5 s / domain, never job boards
  or LinkedIn); job boards only through their official APIs (Reed, Adzuna)
- LinkedIn only as public URLs that appear in web search results
- business emails only through Hunter, inside its monthly cap
"""

from __future__ import annotations

import datetime as dt
import html as _html
import io
import logging
import re
import time
import zipfile
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

from .collect import AGENCY_PHRASES, agency_name_pattern, plain_text, title_matches
from .config import DEFAULT_AGENCY_NAME_WORDS, KNOWN_AGENCY_BRANDS
from .normalise import employer_norm

log = logging.getLogger("research")

CLOSED_WORDS = re.compile(r"(?i)vacancy (?:has )?(?:now )?(?:been )?closed|no longer (?:available|accepting)|this job has expired|"
                          r"position has been filled|applications? (?:are |is )?(?:now )?closed|job (?:is )?no longer")
JOBISH = re.compile(r"(?i)job|career|vacanc|role|position|apply|estimat|manager|engineer|supervisor|recruit")


def _text(html: str, limit: int = 6000) -> str:
    html = re.sub(r"(?is)<(script|style|noscript|svg)[^>]*>.*?</\1>", " ", html)
    return plain_text(html, limit) or ""


def _docx_text(data: bytes, limit: int = 6000) -> str:
    try:
        x = zipfile.ZipFile(io.BytesIO(data)).read("word/document.xml").decode("utf-8", "ignore")
    except Exception:
        return ""
    x = re.sub(r"</w:p>", "\n", x)
    t = _html.unescape(re.sub(r"<[^>]+>", "", x))
    return re.sub(r"\n\s*\n+", "\n", t).strip()[:limit]


class Research:
    def __init__(self, store, settings, *, fetcher=None, claude=None, reed=None, geocoder=None, http=None):
        from .geo import Geocoder
        from .lead_contacts import hunter_for
        from .polite import PoliteFetcher
        self.store, self.settings = store, settings
        self.fetcher = fetcher or PoliteFetcher()
        self.claude = claude
        self.reed = reed
        self.geo = geocoder or Geocoder(store)
        self.http = http or httpx.Client(timeout=30, follow_redirects=True,
                                         headers={"User-Agent": "SmartEmployBot/1.0 (+https://smartemploy.io/bot)"})
        self.hunter = hunter_for(store, settings)
        self._agency = agency_name_pattern(settings.agency_name_words or DEFAULT_AGENCY_NAME_WORDS)
        self._known = {employer_norm(b) for b in (settings.known_agency_brands or KNOWN_AGENCY_BRANDS)}
        self._last: dict[str, float] = {}
        self.ads: dict[str, dict] = {}       # every ad seen, by id, so check_ads can take ids only

    # -- helpers --------------------------------------------------------------------------------
    def _wait(self, key: str, seconds: float) -> None:
        last = self._last.get(key)
        if last is not None and time.monotonic() - last < seconds:
            time.sleep(seconds - (time.monotonic() - last))
        self._last[key] = time.monotonic()

    def _looks_agency(self, name: str, text: str = "") -> bool:
        n = employer_norm(name or "")
        return bool(self._agency.search(name or "")) or n in self._known or \
            any(n.startswith(k + " ") for k in self._known) or bool(AGENCY_PHRASES.search(text or ""))

    # -- job boards (official APIs) ---------------------------------------------------------------
    def search_jobs(self, titles: list[str], location: str, radius_miles: int = 25, max_days_old: int = 90,
                    must_mention: list[str] | None = None) -> dict:
        """Direct-employer ads on Reed + Adzuna whose title matches one of `titles`, plus a count of agency
        ads for the same titles (market demand). must_mention: words one of which must appear in title or text."""
        from .geo import search_area
        town, radius = search_area(location, radius_miles)
        need = re.compile("|".join(re.escape(w) for w in must_mention), re.I) if must_mention else None
        direct: dict[str, dict] = {}
        agency: dict[str, str] = {}
        errors = []
        for kw in titles:
            if self.settings.reed_api_key:
                for posted_by in ("employer", "agency"):
                    skip = 0
                    while skip < 300:
                        self._wait("reed", 1.05)
                        try:
                            r = self.http.get(f"{self.settings.reed_base_url}/search", auth=(self.settings.reed_api_key, ""), params={
                                "keywords": kw, "locationName": town, "distanceFromLocation": radius, "resultsToTake": 100,
                                "resultsToSkip": skip, "postedByDirectEmployer": str(posted_by == "employer").lower(),
                                "postedByRecruitmentAgency": str(posted_by == "agency").lower()})
                            body = r.json()
                        except Exception as exc:
                            errors.append(f"reed: {type(exc).__name__}")
                            break
                        res = body.get("results") or []
                        for j in res:
                            t, desc = j.get("jobTitle") or "", j.get("jobDescription") or ""
                            if not any(title_matches(k, t) for k in titles) or (need and not need.search(t + " " + desc)):
                                continue
                            jid = f"reed:{j['jobId']}"
                            if posted_by == "agency" or self._looks_agency(j.get("employerName"), desc):
                                agency[jid] = j.get("employerName") or ""
                                continue
                            direct[jid] = {"id": jid, "source": "reed", "company": j.get("employerName"), "title": t,
                                           "location": j.get("locationName"), "posted": j.get("date"),
                                           "expires": j.get("expirationDate"), "url": j.get("jobUrl"),
                                           "salary": [j.get("minimumSalary"), j.get("maximumSalary")],
                                           "description": plain_text(desc, 2500)}
                        skip += len(res)
                        if len(res) < 100 or skip >= int(body.get("totalResults") or 0):
                            break
            if self.settings.adzuna_app_id and self.settings.adzuna_app_key:
                self._wait("adzuna", 2.6)
                try:
                    r = self.http.get("https://api.adzuna.com/v1/api/jobs/gb/search/1", params={
                        "app_id": self.settings.adzuna_app_id, "app_key": self.settings.adzuna_app_key, "title_only": kw,
                        "where": town, "distance": round(radius * 1.609), "results_per_page": 50,
                        "max_days_old": max_days_old, "content-type": "application/json"})
                    items = r.json().get("results") or []
                except Exception as exc:
                    errors.append(f"adzuna: {type(exc).__name__}")
                    items = []
                for j in items:
                    emp, t, desc = (j.get("company") or {}).get("display_name") or "", j.get("title") or "", j.get("description") or ""
                    if not any(title_matches(k, t) for k in titles) or (need and not need.search(t + " " + desc)):
                        continue
                    jid = f"adzuna:{j['id']}"
                    if not emp or self._looks_agency(emp, desc):
                        agency[jid] = emp
                        continue
                    direct[jid] = {"id": jid, "source": "adzuna", "company": emp, "title": t,
                                   "location": (j.get("location") or {}).get("display_name"), "posted": (j.get("created") or "")[:10],
                                   "url": f"https://www.adzuna.co.uk/jobs/details/{j['id']}",
                                   "salary": None if str(j.get("salary_is_predicted")) == "1" else [j.get("salary_min"), j.get("salary_max")],
                                   "lat": j.get("latitude"), "lng": j.get("longitude"), "description": plain_text(desc, 2500)}
        self.ads.update(direct)
        names: dict[str, int] = {}
        for n in agency.values():
            names[n or "(unnamed)"] = names.get(n or "(unnamed)", 0) + 1
        return {"searched_around": f"{town} ({radius} miles)", "direct_ads": [{k: v for k, v in a.items() if k != "description"}
                                                                             | {"snippet": (a.get("description") or "")[:240]}
                                                                             for a in direct.values()],
                "agency_ads": len(agency), "agency_names": dict(sorted(names.items(), key=lambda x: -x[1])[:15]),
                "errors": errors}

    def check_ads(self, ad_ids: list[str], industry: str, include: str = "", exclude: str = "",
                  extra_ads: list[dict] | None = None) -> dict:
        """Claude Haiku reads each ad: industry fit, direct employer, not a recruiter, real vacancy, fillable."""
        from . import industries as IND
        from .qualify import judge
        d = IND.get(industry) or IND.definition(industry)
        if include:
            d = {**d, "in": include}
        if exclude:
            d = {**d, "out": exclude}
        ads = [self.ads[i] for i in ad_ids if i in self.ads] + list(extra_ads or [])
        missing = [i for i in ad_ids if i not in self.ads]
        v = judge(self.store, self.claude, d, [{"id": a["id"], "company": a.get("company") or "", "title": a.get("title") or "",
                                                "location": a.get("location") or "", "description": a.get("description") or ""}
                                               for a in ads])
        return {"industry": d["name"], "verdicts": {i: (x.as_dict() if x else None) for i, x in v.items()},
                "not_found": missing}

    # -- employers' own sites ------------------------------------------------------------------------
    def fetch_page(self, url: str) -> dict:
        from .polite import FetchRefused, PageBudget
        try:
            r = self.fetcher.get(url, PageBudget(1))
        except FetchRefused as exc:
            return {"error": str(exc)}
        except httpx.HTTPError as exc:
            return {"error": f"fetch failed: {type(exc).__name__}"}
        ctype = r.headers.get("content-type", "")
        out: dict[str, Any] = {"url": str(r.url), "status": r.status_code, "last_modified": r.headers.get("last-modified")}
        if "wordprocessingml" in ctype or url.lower().endswith(".docx"):
            text = _docx_text(r.content)
        elif "html" in ctype or "text" in ctype or not ctype:
            raw = r.text
            m = re.search(r"(?is)<title[^>]*>(.*?)</title>", raw)
            out["title"] = _html.unescape(m.group(1).strip()) if m else ""
            links = []
            for href, label in re.findall(r'(?is)<a[^>]+href="([^"#]+)"[^>]*>(.*?)</a>', raw):
                full = urljoin(str(r.url), href)
                lab = plain_text(label, 80) or ""
                if JOBISH.search(full + " " + lab) and not re.search(r"\.(css|js|png|jpe?g|svg|gif|woff2?)(\?|$)", full):
                    links.append({"url": full, "text": lab})
            seen, uniq = set(), []
            for l in links:
                if l["url"] not in seen:
                    seen.add(l["url"]); uniq.append(l)
            out["links"] = uniq[:60]
            for k in ("datePosted", "validThrough", "dateModified", "datePublished"):
                m = re.search(rf'"{k}"\s*:\s*"([^"]+)"', raw)
                if m:
                    out[k] = m.group(1)
            text = _text(raw)
        else:
            text = ""
            out["note"] = f"not a page I can read ({ctype})"
        out["closed_wording"] = bool(CLOSED_WORDS.search(text))
        out["text"] = text
        return out

    def sitemap_dates(self, site: str, contains: str = "") -> dict:
        """<lastmod> dates from a site's sitemap(s): when each page last changed."""
        from .polite import FetchRefused, PageBudget
        root = f"{urlsplit(site).scheme or 'https'}://{urlsplit(site).netloc or site.strip('/')}"
        todo, found, fetched = [root + "/sitemap.xml", root + "/sitemap_index.xml"], [], 0
        while todo and fetched < 6:
            u = todo.pop(0)
            try:
                r = self.fetcher.get(u, PageBudget(1))
            except (FetchRefused, httpx.HTTPError):
                continue
            fetched += 1
            if r.status_code != 200:
                continue
            for loc, rest in re.findall(r"(?s)<(?:url|sitemap)>\s*<loc>([^<]+)</loc>(.*?)</(?:url|sitemap)>", r.text):
                lm = re.search(r"<lastmod>([^<]+)</lastmod>", rest)
                if loc.endswith(".xml") and "<sitemap>" in r.text:
                    if not contains or re.search(r"(?i)job|career|vacanc|page|post", loc):
                        todo.append(loc)
                elif not contains or contains.lower() in loc.lower():
                    found.append({"url": loc, "lastmod": lm.group(1)[:10] if lm else None})
        return {"pages": found[:80], "total": len(found)}

    def wayback(self, url: str, prefix: bool = False) -> dict:
        """Internet Archive captures of a page (or every page under a path when prefix=true)."""
        self._wait("wayback", 1.5)
        params: dict[str, Any] = {"url": url, "output": "json", "fl": "timestamp,original,statuscode",
                                  "filter": "statuscode:200", "limit": 400, "collapse": "urlkey" if prefix else "timestamp:6"}
        if prefix:
            params["matchType"] = "prefix"
        try:
            r = self.http.get("https://web.archive.org/cdx/search/cdx", params=params, timeout=40)
            rows = r.json() if r.content.strip() else []
        except Exception as exc:
            return {"error": f"web archive unreachable ({type(exc).__name__})"}
        rows = rows[1:] if rows and rows[0] and rows[0][0] == "timestamp" else rows
        caps = [{"date": f"{x[0][:4]}-{x[0][4:6]}-{x[0][6:8]}", "url": x[1],
                 "archive_link": f"https://web.archive.org/web/{x[0]}/{x[1]}"} for x in rows]
        return {"captures": len(caps), "first": caps[0] if caps else None, "last": caps[-1] if caps else None,
                "sample": caps[:: max(1, len(caps) // 12)][:12]}

    def check_link(self, url: str) -> dict:
        """Is this ad still live? Reed via its API, employer pages politely, other boards with one plain request."""
        m = re.search(r"reed\.co\.uk/jobs/[^/]+/(\d+)", url or "")
        if m and self.reed is not None:
            try:
                d = self.reed.get_details(int(m.group(1)))
                exp = d.get("expirationDate")
                return {"live": True, "via": "reed api", "expires": exp, "title": d.get("jobTitle")}
            except Exception as exc:
                return {"live": False, "via": "reed api", "why": str(exc)[:120]}
        from .polite import is_forbidden_host
        if not is_forbidden_host(url):
            p = self.fetch_page(url)
            if p.get("error"):
                return {"live": None, "why": p["error"]}
            return {"live": p["status"] == 200 and not p["closed_wording"], "status": p["status"],
                    "closed_wording": p["closed_wording"], "title": p.get("title")}
        try:
            r = self.http.get(url, timeout=20)
        except httpx.HTTPError as exc:
            return {"live": None, "why": type(exc).__name__}
        if r.status_code in (401, 403, 429):
            return {"live": None, "status": r.status_code, "why": "the board blocks automated checks: open it by hand"}
        return {"live": r.status_code == 200 and not CLOSED_WORDS.search(r.text[:200000]), "status": r.status_code}

    # -- companies, people, places ------------------------------------------------------------------
    def companies_house(self, name: str = "", number: str = "") -> dict:
        from .ch_client import CachedCompaniesHouse, CompaniesHouseClient
        from .lead_contacts import AGENCY_SIC
        if not self.settings.companies_house_api_key:
            return {"error": "COMPANIES_HOUSE_API_KEY not set"}
        ch = CachedCompaniesHouse(CompaniesHouseClient(self.settings.companies_house_api_key), self.store)
        try:
            if number:
                p = ch.profile(number) or {}
                offs = [o for o in (ch.officers(number) or []) if not o.get("resigned_on")]
                sics = [str(s) for s in p.get("sic_codes") or []]
                return {"number": number, "name": p.get("company_name"), "status": p.get("company_status"),
                        "type": p.get("type"), "created": p.get("date_of_creation"), "sic": sics,
                        "recruitment_business": any(s in AGENCY_SIC for s in sics),
                        "address": ", ".join(v for v in (p.get("registered_office_address") or {}).values() if isinstance(v, str)),
                        "officers": [{"name": o.get("name"), "role": o.get("officer_role"), "appointed": o.get("appointed_on")}
                                     for o in offs][:12]}
            items = ch.search(name) or []
            return {"results": [{"number": i.get("company_number"), "name": i.get("title"), "status": i.get("company_status"),
                                 "address": i.get("address_snippet"), "created": i.get("date_of_creation")} for i in items[:6]]}
        except Exception as exc:
            return {"error": f"companies house: {exc}"[:200]}

    def web_search(self, query: str) -> dict:
        if not self.settings.brave_api_key:
            return {"error": "BRAVE_API_KEY not set"}
        self._wait("brave", 1.1)
        try:
            r = self.http.get("https://api.search.brave.com/res/v1/web/search", params={"q": query, "country": "gb", "count": 10},
                              headers={"X-Subscription-Token": self.settings.brave_api_key, "Accept": "application/json"})
            res = r.json().get("web", {}).get("results") or []
        except Exception as exc:
            return {"error": f"search failed: {type(exc).__name__}"}
        return {"results": [{"title": x.get("title"), "url": x.get("url"), "snippet": plain_text(x.get("description"), 240)}
                            for x in res]}

    def distance(self, from_place: str, to_place: str) -> dict:
        from .geo import miles_between
        a, b = self.geo.geocode(from_place), self.geo.geocode(to_place)
        if not (a and b):
            return {"error": f"couldn't place {'from' if not a else 'to'} location"}
        return {"miles": round(miles_between((a.lat, a.lng), (b.lat, b.lng)), 1), "from": a.name, "to": b.name}

    def find_email(self, domain: str, first_name: str = "", last_name: str = "") -> dict:
        """Hunter, inside the monthly cap. With a name: that person's email. Without: hiring/HR people at the domain."""
        if self.hunter is None:
            return {"error": "HUNTER_API_KEY not set"}
        if first_name and last_name:
            r = self.hunter.find_email(domain, first_name, last_name)
            return {"email": r["email"] if r["verified"] else "", "status": r["status"],
                    "note": "" if r["verified"] else "not verified by Hunter: don't use"}
        people = self.hunter.domain_people(domain)
        return {"people": [{"name": f"{p['first_name']} {p['last_name']}".strip(), "position": p["position"],
                            "email": p["email"] if p["verified"] else "", "verified": p["verified"]} for p in people[:10]]}
