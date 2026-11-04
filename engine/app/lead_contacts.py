"""Decision maker + verified email at each hot-lead company (the hiring employer).

Same idea as the owner's Latvian enrichment (company register -> board member,
Brave -> domain, Hunter -> verified email), for the UK:
  1. Companies House: confirm the company, take its current directors
  2. Brave: the company's own website domain
  3. Hunter: HR / talent people first (they own hiring), else a director's email
Emails count as verified only when Hunter says 'valid'. Suppressed emails are
never stored. Only ltd/llp/plc companies get an email in exports (corporate
subscribers); others keep the name only.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from .agencies import MATCH_THRESHOLD, best_match
from .ch_client import EMAIL_OK_TYPES, current_directors, parse_officer_name

log = logging.getLogger("lead_contacts")

# Companies House SIC codes of recruitment businesses (employment / temp agencies, HR provision).
AGENCY_SIC = {"78100", "78109", "78200", "78300", "74500"}

HIRING_TITLES = re.compile(r"talent|recruit|people|hr\b|human resources|resourcing|hiring|personnel", re.I)


def pick_hunter_person(people: list[dict]) -> dict | None:
    valid = [p for p in people if p["verified"] and p["first_name"]]
    if not valid:
        return None
    valid.sort(key=lambda p: (not HIRING_TITLES.search(p["position"] or ""), -int(p["confidence"] or 0)))
    return valid[0]


def verify_lead_company(store, employer_name: str, employer_norm: str, ch) -> dict[str, Any]:
    """Companies House check: real active company? recruitment SIC code? Stores the result."""
    row: dict[str, Any] = {"employer_norm": employer_norm, "company_name": employer_name, "verified_company": False,
                           "is_agency": False}
    best, conf = best_match(employer_name, ch.search(employer_name))
    row["ch_match_confidence"] = conf
    if best is None:
        row["ch_company_type"] = "not_found"
    elif conf < MATCH_THRESHOLD:
        row["ch_company_type"] = "needs_review"
    else:
        prof = ch.profile(best["company_number"]) or {}
        sics = [str(x) for x in (prof.get("sic_codes") or best.get("sic_codes") or [])]
        status = prof.get("company_status") or best.get("company_status")
        row.update(ch_company_number=best["company_number"], company_name=best.get("title") or employer_name,
                   ch_company_type=prof.get("type") or best.get("company_type"), company_status=status,
                   sic_codes=sics, is_agency=any(x in AGENCY_SIC for x in sics))
        row["verified_company"] = status == "active" and not row["is_agency"]
    existing = store.lead_contacts_for([employer_norm]).get(employer_norm) or {}
    store.save_lead_contact({**existing, **row})
    if row["is_agency"]:
        removed = store.mark_employer_agency(employer_norm)
        log.info("lead company %s is a recruitment business (SIC %s): marked agency, %d hot leads removed",
                 employer_name, row.get("sic_codes"), removed)
    return row


def enrich_lead_company(store, employer_name: str, employer_norm: str, ch, brave=None, hunter=None) -> dict[str, Any]:
    """Verify on Companies House, then (real companies only) find the decision maker and email."""
    row = verify_lead_company(store, employer_name, employer_norm, ch)
    if not row["verified_company"]:
        return row
    directors: list[tuple[str, str, str]] = []
    for o in current_directors(ch.officers(row["ch_company_number"])):
        full, first, last = parse_officer_name(o.get("name", ""))
        if full:
            directors.append((full, first, last))
    if directors:
        row.update(contact_name=directors[0][0], contact_first_name=directors[0][1], contact_role="Director")

    domain = ""
    if brave is not None:
        domain = brave.find_domain(row["company_name"] or employer_name)
        if domain:
            row["website"] = f"https://{domain}"
    if hunter is not None and domain:
        # One domain search (all departments) costs one credit; pick_hunter_person prefers hiring titles.
        person = pick_hunter_person(hunter.domain_people(domain))
        if person and not store.is_suppressed(person["email"]):
            row.update(contact_name=f"{person['first_name']} {person['last_name']}".strip(),
                       contact_first_name=person["first_name"], contact_role=person["position"] or None,
                       email=person["email"], email_status="valid", email_verified=True)
        else:
            for full, first, last in directors[:1]:   # one email-finder credit at most
                r = hunter.find_email(domain, first, last)
                if r["email"] and not store.is_suppressed(r["email"]) and (r["verified"] or not row.get("email")):
                    row.update(contact_name=full, contact_first_name=first, contact_role="Director",
                               email=r["email"], email_status=r["status"], email_verified=r["verified"])
                    if r["verified"]:
                        break
    row["contacts_checked"] = True
    store.save_lead_contact(row)
    log.info("lead contact %s: %s / %s", employer_name, row.get("contact_name"), "email" if row.get("email") else "no email")
    return row


def hunter_for(store, settings):
    """Hunter with the monthly allowance from Settings (free plan: 25 searches a month)."""
    from .web_lookup import Hunter
    if not settings.hunter_api_key:
        return None
    try:
        limit = int(store.setting("hunter_monthly_limit", 25))
    except (TypeError, ValueError):
        limit = 25
    return Hunter(settings.hunter_api_key, store=store, monthly_limit=limit)


def enrich_new_lead_companies(store, settings, limit: int | None = None) -> dict[str, int]:
    """After each rebuild: look up contacts for hot-lead companies not checked yet (best leads first)."""
    if not settings.companies_house_api_key:
        return {"checked": 0, "skipped": "no Companies House key"}  # type: ignore[dict-item]
    from .ch_client import CachedCompaniesHouse, CompaniesHouseClient
    from .web_lookup import Brave, Hunter
    try:
        limit = int(limit if limit is not None else store.setting("lead_contacts_per_run", 20))
    except (TypeError, ValueError):
        limit = 20
    ch = CachedCompaniesHouse(CompaniesHouseClient(settings.companies_house_api_key), store)
    brave = Brave(settings.brave_api_key) if settings.brave_api_key else None
    hunter = hunter_for(store, settings)
    try:
        ch_limit = int(store.setting("lead_ch_checks_per_run", 300))
    except (TypeError, ValueError):
        ch_limit = 300
    verified = agencies = contacts = 0
    # 1) Companies House check for every new hot-lead company (free; removes agencies)
    for lead in store.lead_companies_to_enrich(ch_limit):
        try:
            r = verify_lead_company(store, lead["employer_name"], lead["employer_norm"], ch)
            verified += r["verified_company"]
            agencies += r["is_agency"]
        except Exception as exc:  # one bad company never stops the rest
            log.warning("companies house check for %s failed: %s", lead["employer_name"], exc)
    # 2) Decision maker + email (Brave/Hunter credits): only companies about to go into an open order
    for lead in store.lead_companies_needing_contacts(limit):
        try:
            enrich_lead_company(store, lead["employer_name"], lead["employer_norm"], ch, brave, hunter)
            contacts += 1
        except Exception as exc:
            log.warning("lead contact for %s failed: %s", lead["employer_name"], exc)
    return {"verified": verified, "agencies_removed": agencies, "contacts": contacts}


def email_for_export(contact: dict | None) -> str:
    if not contact or not contact.get("email_verified"):
        return ""
    if contact.get("ch_company_type") not in EMAIL_OK_TYPES:
        return ""
    return contact.get("email") or ""
