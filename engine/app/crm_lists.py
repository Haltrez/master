"""A client agency's CRM export: companies they already have, so we don't sell them their own clients.

Upload any CSV their CRM exports (Bullhorn, Vincere, JobAdder, Firefish, Mercury, Excel...). Columns are
found by their headers; only the company name is required. Matching is by normalised company name,
website domain or Companies House number.
"""

from __future__ import annotations

import csv
import io
import re
from urllib.parse import urlsplit

from .normalise import employer_norm

NAME_HEADERS = ("company name", "company", "client name", "client", "account name", "account", "organisation",
                "organization", "business name", "business", "employer", "name")
DOMAIN_HEADERS = ("website", "web site", "domain", "url", "web", "company website", "homepage")
NUMBER_HEADERS = ("company number", "companies house number", "company no", "company reg", "registration number",
                  "reg number", "crn", "ch number")
OWNER_HEADERS = ("owner", "account owner", "company owner", "consultant", "account manager", "assigned to", "user",
                 "recruiter", "contact owner")
MODES = ("exclude", "flag")


def _pick(headers: list[str], wanted: tuple[str, ...]) -> str | None:
    low = {h.strip().lower(): h for h in headers if h}
    for w in wanted:                       # exact header first, in order of preference
        if w in low:
            return low[w]
    for w in wanted:                       # then a header containing it ("Client Company Name")
        for k, h in low.items():
            if w in k:
                return h
    return None


def domain_of(v: str | None) -> str:
    v = (v or "").strip().lower()
    if not v or " " in v:
        return ""
    if "@" in v:
        v = v.split("@", 1)[1]
    host = urlsplit(v if "//" in v else "//" + v).hostname or ""
    return host[4:] if host.startswith("www.") else host


def _number(v: str | None) -> str:
    v = re.sub(r"\s", "", (v or "").upper())
    return v.zfill(8) if v.isdigit() else v


def parse(text: str) -> tuple[list[dict], dict]:
    text = text.lstrip("﻿")
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    headers = reader.fieldnames or []
    cols = {"name": _pick(headers, NAME_HEADERS), "domain": _pick(headers, DOMAIN_HEADERS),
            "number": _pick(headers, NUMBER_HEADERS), "owner": _pick(headers, OWNER_HEADERS)}
    if not cols["name"]:
        raise ValueError(f"couldn't find a company name column (headers: {', '.join(h for h in headers if h)[:200]})")
    rows, seen = [], set()
    for r in reader:
        name = (r.get(cols["name"]) or "").strip()
        dom = domain_of(r.get(cols["domain"]) if cols["domain"] else "")
        num = _number(r.get(cols["number"]) if cols["number"] else "")
        if not (name or dom or num):
            continue
        key = (employer_norm(name), dom, num)
        if key in seen:
            continue
        seen.add(key)
        rows.append({"name": name, "name_norm": employer_norm(name) if name else None, "domain": dom or None,
                     "ch_number": num or None, "owner": (r.get(cols["owner"]) or "").strip() if cols["owner"] else None})
    return rows, cols


def replace_list(store, agency_id: int, text: str, mode: str | None = None) -> dict:
    rows, cols = parse(text)
    with store.conn.transaction():
        store.q("delete from agency_crm_companies where agency_id = %s", (agency_id,))
        with store.conn.cursor() as cur:
            cur.executemany("""insert into agency_crm_companies (agency_id, name, name_norm, domain, ch_number, owner)
                               values (%s, %s, %s, %s, %s, %s)""",
                            [(agency_id, r["name"], r["name_norm"], r["domain"], r["ch_number"], r["owner"]) for r in rows])
        if mode:
            set_mode(store, agency_id, mode)
    return {**summary(store, agency_id), "columns_found": {k: v for k, v in cols.items() if v}}


def set_mode(store, agency_id: int, mode: str) -> None:
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    store.q("update agencies set crm_match_mode = %s where id = %s", (mode, agency_id))


def summary(store, agency_id: int) -> dict:
    r = store.one("""select count(*) as companies, max(uploaded_at) as uploaded_at,
                            count(*) filter (where owner is not null and owner <> '') as with_owner,
                            (select crm_match_mode from agencies where id = %s) as mode
                     from agency_crm_companies where agency_id = %s""", (agency_id, agency_id))
    return dict(r or {})


def clear(store, agency_id: int) -> None:
    store.q("delete from agency_crm_companies where agency_id = %s", (agency_id,))


class CrmIndex:
    """Fast lookups for one agency's list while leads are picked."""

    def __init__(self, store, agency_id: int | None):
        self.mode = "exclude"
        self.by_norm: dict[str, str] = {}
        self.by_domain: dict[str, str] = {}
        self.by_number: dict[str, str] = {}
        if not agency_id:
            return
        a = store.one("select crm_match_mode from agencies where id = %s", (agency_id,))
        self.mode = (a or {}).get("crm_match_mode") or "exclude"
        for r in store.q("select name_norm, domain, ch_number, owner from agency_crm_companies where agency_id = %s",
                         (agency_id,)):
            owner = r["owner"] or ""
            if r["name_norm"]:
                self.by_norm.setdefault(r["name_norm"], owner)
            if r["domain"]:
                self.by_domain.setdefault(r["domain"], owner)
            if r["ch_number"]:
                self.by_number.setdefault(r["ch_number"], owner)

    def __bool__(self) -> bool:
        return bool(self.by_norm or self.by_domain or self.by_number)

    def match(self, employer_norm_: str, contact: dict | None = None) -> str | None:
        """None = not in their CRM; otherwise the owner ('' when the list has no owner column)."""
        if employer_norm_ in self.by_norm:
            return self.by_norm[employer_norm_]
        c = contact or {}
        num = _number(c.get("ch_company_number"))
        if num and num in self.by_number:
            return self.by_number[num]
        dom = domain_of(c.get("website"))
        if dom and dom in self.by_domain:
            return self.by_domain[dom]
        return None
