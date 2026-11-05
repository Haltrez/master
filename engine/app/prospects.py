"""People exported from Smartlead's Prospect page (or Apollo, LinkedIn Sales Navigator...) as a CSV, added as
contacts at the agencies we already track.

Smartlead's API has no Prospect endpoints, so this is a file upload. Columns are found by their headers.
An email is needed, plus a company name or website to match the agency. People at agencies we don't track
(no ads in your territories, so no hot leads to pitch) are listed back, not added. Job titles decide the
campaign (outreach.audience); suppressed emails are never stored.
"""

from __future__ import annotations

import csv
import io

from .crm_lists import _pick, domain_of
from .normalise import employer_norm

EMAIL_HEADERS = ("email", "work email", "email address", "business email", "professional email", "contact email")
FIRST_HEADERS = ("first name", "firstname", "first_name", "given name")
LAST_HEADERS = ("last name", "lastname", "last_name", "surname", "family name")
FULL_HEADERS = ("full name", "name", "contact name", "person name")
TITLE_HEADERS = ("job title", "title", "designation", "position", "role", "headline")
COMPANY_HEADERS = ("company name", "company", "organisation", "organization", "account name", "employer",
                   "current company")
DOMAIN_HEADERS = ("company domain", "company website", "website", "domain", "company url", "url")
LINKEDIN_HEADERS = ("linkedin", "linkedin url", "person linkedin url", "linkedin profile", "profile url")
STATUS_HEADERS = ("email status", "email verification", "verification status", "email validation", "status",
                  "verified")
GOOD = ("valid", "verified", "deliverable", "safe", "true", "yes", "ok")
BAD = ("invalid", "undeliverable", "bounce", "risky", "catch", "accept_all", "unknown", "false", "no", "unverified")


def parse(text: str) -> tuple[list[dict], dict]:
    text = text.lstrip("﻿")
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    headers = reader.fieldnames or []
    linkedin = _pick(headers, LINKEDIN_HEADERS)
    company_cols = [h for h in headers if h and "linkedin" not in h.lower()]
    email_cols = [h for h in headers if h and not any(w in h.lower() for w in ("status", "verif", "valid", "score"))]
    status_cols = [h for h in headers if h and any(w in h.lower() for w in ("email", "verif", "valid"))]
    cols = {"email": _pick(email_cols, EMAIL_HEADERS), "first": _pick(headers, FIRST_HEADERS),
            "last": _pick(headers, LAST_HEADERS), "full": _pick(headers, FULL_HEADERS),
            "title": _pick(headers, TITLE_HEADERS), "company": _pick(company_cols, COMPANY_HEADERS),
            "domain": _pick(company_cols, DOMAIN_HEADERS), "linkedin": linkedin, "status": _pick(status_cols, STATUS_HEADERS)}
    for k in ("first", "last", "full", "company", "domain", "status", "title"):
        if cols[k] and cols[k] == cols["email"]:
            cols[k] = None                 # "Email Status" must not become the email column, and vice versa
    if cols["full"] in (cols["company"], cols["first"], cols["last"]):
        cols["full"] = None
    if not cols["email"]:
        raise ValueError(f"couldn't find an email column (headers: {', '.join(h for h in headers if h)[:200]})")
    if not (cols["company"] or cols["domain"]):
        raise ValueError("couldn't find a company name or website column to match the agency")
    g = lambda r, k: (r.get(cols[k]) or "").strip() if cols[k] else ""   # noqa: E731
    rows, seen = [], set()
    for r in reader:
        email = g(r, "email").lower()
        if "@" not in email or email in seen:
            continue
        seen.add(email)
        first, last = g(r, "first"), g(r, "last")
        full = " ".join(x for x in (first, last) if x) or g(r, "full")
        if not first and full:
            first = full.split()[0]
        status = g(r, "status").lower()
        rows.append({"email": email, "first_name": first, "full_name": full or email.split("@")[0],
                     "title": g(r, "title"), "company": g(r, "company"), "domain": domain_of(g(r, "domain")),
                     "email_domain": domain_of(email), "linkedin": g(r, "linkedin") or None,
                     "status": "valid" if any(w in status for w in GOOD) and not any(w in status for w in BAD)
                     else ("bad" if status else "")})
    return rows, cols


def import_contacts(store, text: str, *, trust_unlabelled: bool = True) -> dict:
    """trust_unlabelled: a file with no email-status column counts as verified (Smartlead Prospect only
    reveals emails it has checked). Rows marked anything but valid are added unverified, so they're never pushed."""
    from .outreach import ROLE_LABEL, audience
    rows, cols = parse(text)
    agencies = store.q("select id, name, name_norm, ch_company_name, website from agencies where stage <> 'do_not_contact'")
    by_name, by_domain = {}, {}
    for a in agencies:
        for n in (a["name_norm"], employer_norm(a["ch_company_name"] or "")):
            if n:
                by_name.setdefault(n, a)
        d = domain_of(a["website"])
        if d:
            by_domain.setdefault(d, a)
    out = {"rows": len(rows), "added": 0, "updated": 0, "unverified": 0, "skipped_title": 0, "suppressed": 0,
           "by_campaign": {k: 0 for k in ROLE_LABEL}, "not_tracked": [], "columns": {k: v for k, v in cols.items() if v}}
    for p in rows:
        ag = (by_domain.get(p["domain"]) or by_domain.get(p["email_domain"])
              or by_name.get(employer_norm(p["company"]) if p["company"] else ""))
        if ag is None:
            if p["company"] and p["company"] not in out["not_tracked"]:
                out["not_tracked"].append(p["company"])
            continue
        kind = audience(p["title"])
        if kind not in ROLE_LABEL:
            out["skipped_title"] += 1
            continue
        if store.is_suppressed(p["email"]):
            out["suppressed"] += 1
            continue
        verified = p["status"] == "valid" or (p["status"] == "" and not cols["status"] and trust_unlabelled)
        out["unverified"] += not verified
        fields = {"email": p["email"], "email_verified": verified, "email_status": "valid" if verified else "unverified"}
        existing = store.one("""select * from contacts where agency_id = %s and (lower(email) = %s or lower(full_name) = lower(%s))
                                order by (lower(email) = %s) desc limit 1""", (ag["id"], p["email"], p["full_name"], p["email"]))
        if existing:
            upd = {k: v for k, v in fields.items() if not existing.get("email_verified") or k != "email"}
            if not existing.get("role") and p["title"]:
                upd["role"] = p["title"]
            if p["linkedin"] and not existing.get("linkedin_url"):
                upd["linkedin_url"] = p["linkedin"]
            store.update_contact(existing["id"], upd)
            out["updated"] += 1
        else:
            store.add_contact({"agency_id": ag["id"], "full_name": p["full_name"], "first_name": p["first_name"],
                               "role": p["title"] or None, "linkedin_url": p["linkedin"], "source": "prospect", **fields})
            out["added"] += 1
        out["by_campaign"][kind] += 1
    out["not_tracked"] = out["not_tracked"][:50]
    return out
