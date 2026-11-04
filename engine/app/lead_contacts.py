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

HIRING_TITLES = re.compile(r"talent|recruit|people|hr\b|human resources|resourcing|hiring|personnel", re.I)


def pick_hunter_person(people: list[dict]) -> dict | None:
    valid = [p for p in people if p["verified"] and p["first_name"]]
    if not valid:
        return None
    valid.sort(key=lambda p: (not HIRING_TITLES.search(p["position"] or ""), -int(p["confidence"] or 0)))
    return valid[0]


def enrich_lead_company(store, employer_name: str, employer_norm: str, ch, brave=None, hunter=None) -> dict[str, Any]:
    row: dict[str, Any] = {"employer_norm": employer_norm, "company_name": employer_name}
    best, conf = best_match(employer_name, ch.search(employer_name)) if ch else (None, 0.0)
    directors: list[tuple[str, str, str]] = []
    if best is not None and conf >= MATCH_THRESHOLD:
        row.update(ch_company_number=best["company_number"], ch_company_type=best.get("company_type"),
                   ch_match_confidence=conf, company_name=best.get("title") or employer_name)
        for o in current_directors(ch.officers(best["company_number"])):
            full, first, last = parse_officer_name(o.get("name", ""))
            if full:
                directors.append((full, first, last))
    else:
        row.update(ch_match_confidence=conf, ch_company_type="not_found" if best is None else "needs_review")
    if directors:
        row.update(contact_name=directors[0][0], contact_first_name=directors[0][1], contact_role="Director")

    domain = ""
    if brave is not None:
        domain = brave.find_domain(row["company_name"] or employer_name)
        if domain:
            row["website"] = f"https://{domain}"
    if hunter is not None and domain:
        person = pick_hunter_person(hunter.domain_people(domain, "hr")) or \
            pick_hunter_person(hunter.domain_people(domain, "executive"))
        if person and not store.is_suppressed(person["email"]):
            row.update(contact_name=f"{person['first_name']} {person['last_name']}".strip(),
                       contact_first_name=person["first_name"], contact_role=person["position"] or None,
                       email=person["email"], email_status="valid", email_verified=True)
        else:
            for full, first, last in directors[:2]:
                r = hunter.find_email(domain, first, last)
                if r["email"] and not store.is_suppressed(r["email"]) and (r["verified"] or not row.get("email")):
                    row.update(contact_name=full, contact_first_name=first, contact_role="Director",
                               email=r["email"], email_status=r["status"], email_verified=r["verified"])
                    if r["verified"]:
                        break
    store.save_lead_contact(row)
    log.info("lead contact %s: %s / %s", employer_name, row.get("contact_name"), "email" if row.get("email") else "no email")
    return row


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
    hunter = Hunter(settings.hunter_api_key) if settings.hunter_api_key else None
    done = 0
    for lead in store.lead_companies_to_enrich(limit):
        try:
            enrich_lead_company(store, lead["employer_name"], lead["employer_norm"], ch, brave, hunter)
            done += 1
        except Exception as exc:  # one bad company never stops the rest
            log.warning("lead contact for %s failed: %s", lead["employer_name"], exc)
    return {"checked": done}


def email_for_export(contact: dict | None) -> str:
    if not contact or not contact.get("email_verified"):
        return ""
    if contact.get("ch_company_type") not in EMAIL_OK_TYPES:
        return ""
    return contact.get("email") or ""
