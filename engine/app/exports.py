"""CSV exports. Outreach rows only for contacts we're allowed to email."""

from __future__ import annotations

import csv
import io
from collections import Counter
from typing import Any

from .agencies import email_allowed
from .templates import lead_vars, niche_and_city

OUTREACH_COLUMNS = ["email", "first_name", "agency", "niche", "city", "lead1_employer", "lead1_role", "lead1_count",
                    "lead1_since", "lead2_employer", "lead2_role", "lead2_count", "lead3_employer", "lead3_role",
                    "lead3_count"]


def outreach_rows(store, *, search_id: int | None = None, stage: str = "new") -> tuple[list[dict], Counter]:
    rows: list[dict] = []
    skipped: Counter = Counter()
    suppressed = store.suppressed_hashes()
    from .pg import email_hash

    for ag in store.agencies_list(search_id=search_id):
        if ag["stage"] != stage:
            skipped["not_at_stage"] += 1
            continue
        if not email_allowed(ag):
            skipped["company_type_not_ltd_llp_plc"] += 1
            continue
        sids = [search_id] if search_id else store.agency_search_ids(ag["id"])
        search = store.search_row(sids[0]) if sids else None
        niche, city = niche_and_city(search)
        leads = store.top_hot_leads_for_search(search["id"], 3) if search else []
        lv = lead_vars(leads)
        for c in store.contacts(ag["id"]):
            if c["do_not_contact"]:
                skipped["do_not_contact"] += 1
                continue
            if not (c.get("email") and c.get("email_verified")):
                skipped["no_verified_email"] += 1
                continue
            if email_hash(c["email"]) in suppressed:
                skipped["suppressed"] += 1
                continue
            row = {"email": c["email"], "first_name": c.get("first_name") or "", "agency": ag["name"],
                   "niche": niche, "city": city, **{k: lv.get(k, "") for k in OUTREACH_COLUMNS[5:]}}
            if any(row[k] in ("", None) for k in OUTREACH_COLUMNS):
                skipped["missing_template_variable"] += 1
                continue
            rows.append(row)
    return rows, skipped


def to_csv(columns: list[str], rows: list[dict[str, Any]]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow(r)
    return buf.getvalue()
