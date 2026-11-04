"""Extract job titles, locations and links from an employer's careers page.

Stores only: job title, location text, job URL and a hash of the page text.
No names, emails or other personal data from the page are kept.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

SKIP_TAGS = {"script", "style", "noscript", "svg", "template", "nav", "header", "footer", "form",
             "select", "option", "iframe"}
BLOCK_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6", "p", "li", "a", "span", "div", "td", "th", "dt", "dd",
              "strong", "b", "em", "label", "section", "article", "button", "br", "tr"}
VOID_TAGS = {"br", "img", "hr", "input", "meta", "link", "source", "area", "base", "col", "embed", "wbr"}

HEAD_NOUNS = {
    "engineer", "manager", "estimator", "electrician", "supervisor", "technician", "coordinator",
    "ordinator", "adviser", "advisor", "labourer", "mate", "surveyor", "designer", "planner", "buyer",
    "operative", "apprentice", "administrator", "assistant", "director", "foreman", "fitter",
    "installer", "plumber", "consultant", "analyst", "officer", "controller", "accountant", "trainee",
    "draughtsman", "draughtsperson", "inspector", "tester", "executive", "specialist", "clerk",
    "receptionist", "driver", "storeman", "modeller", "programmer", "developer", "scheduler",
    "leader", "lead", "handyman", "joiner", "bricklayer", "welder", "mechanic", "commissioner",
    "agent", "representative", "partner", "head", "graduate", "intern",
}
# Text containing these is marketing copy, not a job title.
NOT_TITLE_WORDS = {"our", "we", "you", "your", "us", "news", "services", "service's", "about", "contact",
                   "careers", "career", "join", "meet", "register", "apply", "view", "read", "more",
                   "cookie", "cookies", "privacy", "policy", "terms", "login", "sign"}
TRAILING_NOISE = re.compile(r"[\s\-–—|:]*\b(job|jobs|vacancy|vacancies|role|position|opportunity)\s*$", re.I)
LOCATION_SPLIT = re.compile(r"\s+[-–—|]+\s+")
NOT_LOCATION_WORDS = {"maternity", "cover", "ftc", "contract", "permanent", "temporary", "temp",
                      "part", "full", "time", "hybrid", "remote", "electrical", "mechanical", "m&e",
                      "hvac", "building", "services", "urgent", "immediate", "start", "new"}
EMAIL_OR_PHONE = re.compile(r"\S+@\S+|\+?\d[\d\s()-]{7,}\d")
DOC_LINK = re.compile(r"\.(docx?|pdf|rtf|odt)(\?|#|$)", re.I)
IMAGE_LINK = re.compile(r"\.(jpe?g|png|gif|webp|avif|svg)(\?|#|$)", re.I)
# "Karl Killen, Contracts Manager": a named person, never a vacancy.
PERSON_PREFIX = re.compile(r"^\s*([A-Z][a-z'’-]+(?:\s+[A-Z][a-z'’-]+){1,2})\s*[,–—-]\s+\S")
SKIP_HREF = re.compile(r"^(mailto:|tel:|javascript:|#)", re.I)
JS_APP_MARKERS = re.compile(r'id="(root|app|__next|__nuxt)"|enable javascript|requires javascript|ng-app', re.I)


@dataclass
class ExtractedJob:
    title: str
    location_text: str | None
    url: str | None
    keywords_found: list[str] = field(default_factory=list)
    date_posted: str | None = None


@dataclass
class PageExtract:
    jobs: list[ExtractedJob]
    text: str
    page_hash: str
    js_only: bool
    ats_links: list[str]


class _Flattener(HTMLParser):
    """Turns HTML into an ordered list of items: ('text', str, href) and ('link', href, text)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.items: list[tuple[str, str, str | None]] = []
        self.jsonld: list[str] = []
        self._skip = 0
        self._stack: list[str] = []
        self._buf: list[str] = []
        self._href_stack: list[str | None] = []
        self._in_jsonld = False
        self._jsonld_buf: list[str] = []
        self.script_count = 0

    @property
    def _href(self) -> str | None:
        for h in reversed(self._href_stack):
            if h:
                return h
        return None

    def _flush(self) -> None:
        text = " ".join("".join(self._buf).split())
        self._buf = []
        if text:
            self.items.append(("text", text, self._href))

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        if tag == "script":
            self.script_count += 1
            if (a.get("type") or "").lower() == "application/ld+json":
                self._in_jsonld = True
                self._jsonld_buf = []
                return
        if tag in VOID_TAGS:
            if tag == "br":
                self._flush()
            return
        if tag in SKIP_TAGS:
            self._skip += 1
            self._stack.append(tag)
            return
        if tag in BLOCK_TAGS:
            self._flush()
        self._stack.append(tag)
        if tag == "a":
            href = a.get("href")
            self._href_stack.append(href)
            if href and not self._skip:
                self.items.append(("link", href, None))
        else:
            self._href_stack.append(None)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._in_jsonld:
            self.jsonld.append("".join(self._jsonld_buf))
            self._in_jsonld = False
            return
        if tag in VOID_TAGS or tag not in self._stack:
            return
        while self._stack:
            t = self._stack.pop()
            if t in SKIP_TAGS:
                self._skip -= 1
            else:
                if t in BLOCK_TAGS:
                    self._flush()
                if self._href_stack:
                    self._href_stack.pop()
            if t == tag:
                break

    def handle_data(self, data: str) -> None:
        if self._in_jsonld:
            self._jsonld_buf.append(data)
        elif not self._skip:
            self._buf.append(data)

    def close(self) -> None:
        super().close()
        self._flush()


def _tokens(text: str) -> list[str]:
    return re.sub(r"[^\w&']+", " ", text.lower()).split()


def singular(token: str) -> str:
    if len(token) > 4 and token.endswith("s") and not token.endswith("ss"):
        stem = token[:-1]
        if stem in HEAD_NOUNS or stem.endswith(("er", "or", "ian", "ant", "ate", "ist")):
            return stem
    return token


def clean_title(raw: str) -> tuple[str, str | None]:
    """Return (title, location-from-title). Drops trailing 'Job', '- Manchester' suffixes, emails."""
    t = EMAIL_OR_PHONE.sub(" ", raw)
    t = " ".join(t.replace("‍", " ").split())
    t = TRAILING_NOISE.sub("", t).strip(" -–—|:")
    location = None
    parts = LOCATION_SPLIT.split(t)
    if len(parts) > 1:
        tail = parts[-1]
        tail_tokens = _tokens(tail)
        if (1 <= len(tail_tokens) <= 4 and not any(singular(x) in HEAD_NOUNS for x in tail_tokens)
                and not any(x in NOT_LOCATION_WORDS for x in tail_tokens)):
            location = tail.strip()
            t = " - ".join(parts[:-1]).strip()
    m = re.search(r"\(([^)]*)\)\s*$", t)
    if m and location is None:
        inner = _tokens(m.group(1))
        if 1 <= len(inner) <= 3 and not any(x in NOT_LOCATION_WORDS for x in inner) \
                and not any(singular(x) in HEAD_NOUNS for x in inner):
            location = m.group(1).strip()
            t = t[: m.start()].strip()
    return t, location


def looks_like_person(text: str) -> bool:
    m = PERSON_PREFIX.match(text)
    if not m:
        return False
    words = _tokens(m.group(1))
    return not any(singular(w) in HEAD_NOUNS or w in NOT_LOCATION_WORDS for w in words)


def looks_like_job_title(text: str) -> bool:
    if not text or len(text) > 90 or "@" in text or text.endswith((".", "?", "!")):
        return False
    if looks_like_person(text):
        return False
    title, _ = clean_title(text)
    toks = _tokens(title)
    if not 1 <= len(toks) <= 10:
        return False
    if any(t in NOT_TITLE_WORDS for t in toks):
        return False
    if sum(c.isdigit() for c in title) > 2:
        return False
    sing = [singular(t) for t in toks]
    if sing[-1] in HEAD_NOUNS:
        return True
    # "Estimator (Electrical)", "Engineer - Building Services"
    return len(toks) <= 6 and sing[0] in HEAD_NOUNS and len(toks) > 1


def _norm_url(href: str, base: str) -> str:
    u = urlsplit(urljoin(base, href.strip()))
    return urlunsplit((u.scheme, u.netloc.lower(), u.path, u.query, ""))


def _jsonld_jobs(blobs: list[str], base: str) -> list[ExtractedJob]:
    jobs: list[ExtractedJob] = []

    def walk(node: Any) -> None:
        if isinstance(node, list):
            for n in node:
                walk(n)
        elif isinstance(node, dict):
            types = node.get("@type")
            types = types if isinstance(types, list) else [types]
            if "JobPosting" in types and node.get("title"):
                jobs.append(ExtractedJob(
                    title=clean_title(str(node["title"]))[0],
                    location_text=_jsonld_location(node.get("jobLocation")),
                    url=_norm_url(str(node.get("url") or base), base),
                    date_posted=str(node.get("datePosted"))[:10] if node.get("datePosted") else None,
                ))
            for k in ("@graph", "itemListElement", "item"):
                if k in node:
                    walk(node[k])

    for blob in blobs:
        try:
            walk(json.loads(blob))
        except ValueError:
            continue
    return jobs


def _jsonld_location(loc: Any) -> str | None:
    if isinstance(loc, list):
        loc = loc[0] if loc else None
    if not isinstance(loc, dict):
        return None
    addr = loc.get("address")
    if isinstance(addr, str):
        return addr
    if isinstance(addr, dict):
        parts = [addr.get("addressLocality"), addr.get("postalCode")]
        return ", ".join(str(p) for p in parts if p) or None
    return None


def page_text_hash(text: str) -> str:
    return hashlib.sha256(" ".join(text.split()).encode("utf-8")).hexdigest()


def find_keywords(text: str, keywords: list[str]) -> list[str]:
    low = " " + re.sub(r"\s+", " ", text.lower()) + " "
    found = []
    for kw in keywords:
        k = kw.lower().strip()
        if k and re.search(r"(?<![\w&])" + re.escape(k) + r"(?![\w&])", low):
            found.append(kw)
    return found


def extract(html: str, base_url: str, sector_keywords: list[str] | None = None) -> PageExtract:
    from .ats import detect_ats  # local import to avoid a cycle

    p = _Flattener()
    p.feed(html)
    p.close()
    text = "\n".join(i[1] for i in p.items if i[0] == "text")
    kws = find_keywords(text, sector_keywords or [])

    jobs = _jsonld_jobs(p.jsonld, base_url)
    if not jobs:
        jobs = _heuristic_jobs(p.items, base_url)
    for j in jobs:
        j.keywords_found = sorted(set(j.keywords_found) | set(kws) | set(find_keywords(j.title, sector_keywords or [])))

    ats_links = sorted({_norm_url(i[1], base_url) for i in p.items
                        if i[0] == "link" and detect_ats(_norm_url(i[1], base_url)) is not None})
    js_only = not jobs and (len(text) < 400 and (p.script_count > 0 or bool(JS_APP_MARKERS.search(html)))
                            or bool(re.search(r"enable javascript|requires javascript", text, re.I)))
    return PageExtract(jobs=jobs, text=text, page_hash=page_text_hash(text), js_only=js_only, ats_links=ats_links)


def _heuristic_jobs(items: list[tuple[str, str, str | None]], base_url: str) -> list[ExtractedJob]:
    jobs: list[ExtractedJob] = []
    seen_titles: set[str] = set()
    pending: ExtractedJob | None = None
    pending_age = 0
    for kind, value, href in items:
        if kind == "text":
            if looks_like_job_title(value):
                title, loc = clean_title(value)
                key = title.lower()
                if key in seen_titles:
                    pending = None
                    continue
                seen_titles.add(key)
                url = None
                if href and not SKIP_HREF.match(href) and not IMAGE_LINK.search(href):
                    url = _norm_url(href, base_url)
                job = ExtractedJob(title=title, location_text=loc, url=url)
                jobs.append(job)
                pending = None if url else job
                pending_age = 0
            elif pending is not None:
                pending_age += 1
                if pending_age > 12:
                    pending = None
        elif kind == "link" and pending is not None and not SKIP_HREF.match(value):
            if not IMAGE_LINK.search(value):
                pending.url = _norm_url(value, base_url)
            pending = None
    # On a page where vacancies have their own links, unlinked titles are
    # bullet lists of "roles we're always looking for", not live vacancies.
    if any(j.url for j in jobs):
        jobs = [j for j in jobs if j.url]
    # Jobs without their own link, or sharing a link with another job, get a
    # stable per-title URL on the careers page so (employer_id, url) stays unique.
    counts: dict[str | None, int] = {}
    for j in jobs:
        counts[j.url] = counts.get(j.url, 0) + 1
    for j in jobs:
        if j.url is None or counts[j.url] > 1 or j.url == _norm_url(base_url, base_url):
            j.url = _norm_url(base_url, base_url) + "#" + re.sub(r"[^a-z0-9]+", "-", j.title.lower()).strip("-")
    return jobs


def is_document_link(url: str | None) -> bool:
    return bool(url and DOC_LINK.search(url))


def is_synthetic_url(url: str | None) -> bool:
    return bool(url and "#" in url)


def display_url(url: str | None) -> str:
    return (url or "").split("#", 1)[0]


def detail_extract(html: str, base_url: str, sector_keywords: list[str]) -> tuple[str | None, str | None, list[str]]:
    """From a job detail page: (location, date_posted, sector keywords found)."""
    p = _Flattener()
    p.feed(html)
    p.close()
    text = "\n".join(i[1] for i in p.items if i[0] == "text")
    jobs = _jsonld_jobs(p.jsonld, base_url)
    loc = jobs[0].location_text if jobs else None
    posted = jobs[0].date_posted if jobs else None
    return loc, posted, find_keywords(text, sector_keywords)
