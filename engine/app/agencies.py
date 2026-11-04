"""Prospect agencies: built from agency listings, enriched from Companies House,
then (optionally) website via Brave and verified director emails via Hunter.

Compliance (shown in the CRM, used by every export and draft):
email outreach only to agencies confirmed on Companies House as ltd, llp or plc
with a trusted match (confidence >= 0.85). "Not found" is never allowed:
sole traders and partnerships aren't companies.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any

from .ch_client import (EMAIL_OK_TYPES, CachedCompaniesHouse, current_directors, name_similarity,
                        parse_officer_name)

log = logging.getLogger("agencies")

MATCH_THRESHOLD = 0.85
HUNTER_MAX_PER_AGENCY = 2


def email_allowed(agency: dict[str, Any]) -> bool:
    return (agency.get("ch_company_type") in EMAIL_OK_TYPES and not agency.get("ch_review_needed")
            and float(agency.get("ch_match_confidence") or 0) >= MATCH_THRESHOLD
            and agency.get("stage") != "do_not_contact")


def compliance_label(agency: dict[str, Any]) -> str:
    if agency.get("stage") == "do_not_contact":
        return "do not contact"
    if email_allowed(agency):
        return agency["ch_company_type"]
    if agency.get("ch_checked_at") is None:
        return "check before emailing (not checked)"
    if agency.get("ch_company_type") == "not_found":
        return "check before emailing (not on Companies House)"
    if agency.get("ch_review_needed"):
        return "check before emailing (match needs review)"
    return f"check before emailing ({agency.get('ch_company_type') or 'unknown'})"


def rebuild_agencies(store, *, lookback_days: int = 90, now: dt.datetime | None = None) -> dict[str, int]:
    now = now or dt.datetime.now(dt.timezone.utc)
    out = store.rebuild_agencies_sql(now - dt.timedelta(days=lookback_days))
    log.info("agencies: %d with ads in the window (%d new)", out["agencies"], out["new"])
    return out


def best_match(name: str, items: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, float]:
    best, conf = None, 0.0
    for it in items:
        if (it.get("company_status") or "").lower() not in ("active", ""):
            continue
        c = name_similarity(name, it.get("title") or "")
        if c > conf:
            best, conf = it, c
    return best, conf


def enrich_agency(store, agency_id: int, ch: CachedCompaniesHouse, brave=None, hunter=None) -> dict[str, Any]:
    ag = store.get_agency(agency_id)
    if ag is None:
        raise ValueError(f"agency {agency_id} not found")
    now = dt.datetime.now(dt.timezone.utc)
    result: dict[str, Any] = {"agency_id": agency_id, "name": ag["name"]}

    best, conf = best_match(ag["name"], ch.search(ag["name"]))
    fields: dict[str, Any] = {"ch_checked_at": now, "ch_match_confidence": conf}
    if best is None:
        fields.update(ch_company_type="not_found", ch_company_status=None, ch_company_number=None,
                      ch_company_name=None, ch_review_needed=True)
        result["companies_house"] = "not found"
    else:
        number = best["company_number"]
        fields.update(ch_company_number=number, ch_company_name=best.get("title"),
                      ch_company_type=best.get("company_type"), ch_company_status=best.get("company_status"))
        if conf < MATCH_THRESHOLD:
            fields["ch_review_needed"] = True
            result["companies_house"] = f"low-confidence match ({conf:.2f}): {best.get('title')}, needs review"
        else:
            prof = ch.profile(number)
            fields.update(ch_company_type=prof.get("type") or best.get("company_type"),
                          ch_company_status=prof.get("company_status") or best.get("company_status"),
                          ch_review_needed=False)
            added = 0
            for o in current_directors(ch.officers(number)):
                full, first, last = parse_officer_name(o.get("name", ""))
                if full and store.add_contact({"agency_id": agency_id, "full_name": full, "first_name": first,
                                               "role": (o.get("officer_role") or "director").replace("-", " ").title(),
                                               "source": "companies_house"}):
                    added += 1
            result["companies_house"] = f"matched {best.get('title')} ({fields['ch_company_type']}, {conf:.2f})"
            result["directors_added"] = added
    store.update_agency(agency_id, fields)

    # Website (Brave) and verified emails (Hunter), only for a trusted company.
    ag = store.get_agency(agency_id)
    if brave is not None and not ag.get("website"):
        domain = brave.find_domain(ag.get("ch_company_name") or ag["name"])
        if domain:
            store.update_agency(agency_id, {"website": f"https://{domain}"})
            result["website"] = domain
            ag["website"] = f"https://{domain}"
    if hunter is not None and ag.get("website") and email_allowed(ag):
        from urllib.parse import urlsplit
        domain = (urlsplit(ag["website"]).hostname or "").removeprefix("www.")
        found = 0
        for c in [c for c in store.contacts(agency_id) if not c.get("email") and not c.get("do_not_contact")]:
            if found >= HUNTER_MAX_PER_AGENCY:
                break
            parts = c["full_name"].split()
            if len(parts) < 2:
                continue
            r = hunter.find_email(domain, parts[0], parts[-1])
            if r["email"] and not store.is_suppressed(r["email"]):
                store.update_contact(c["id"], {"email": r["email"], "email_verified": r["verified"],
                                               "email_status": r["status"]})
                found += 1
        result["emails_found"] = found
    log.info("enrich agency %d '%s': %s", agency_id, ag["name"], result)
    return result
