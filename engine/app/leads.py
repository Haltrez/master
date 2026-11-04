"""Excel-style lead rows: one per hot lead, used for the owner's CSV and the
client Google Sheets (Make.com). Columns mirror the owner's Latvian sheet:
company, role, salary, ad link, previous ad link, decision maker, email and a
ready-to-send email from the client agency to the hiring company."""

from __future__ import annotations

import datetime as dt
from typing import Any

from .lead_contacts import email_for_export
from .templates import month_year, niche_and_city, render

LEAD_COLUMNS = ["date_added", "company", "role", "max_salary", "times_posted", "first_seen", "location", "ad_link",
                "previous_ad_link", "decision_maker", "decision_maker_role", "email", "suggested_email", "score"]
CSV_HEADERS = {"date_added": "Date added", "company": "Company", "role": "Role", "max_salary": "Max salary",
               "times_posted": "Times posted", "first_seen": "First seen", "location": "Location",
               "ad_link": "Ad link", "previous_ad_link": "Previous ad", "decision_maker": "Decision maker",
               "decision_maker_role": "Their role", "email": "Email", "suggested_email": "Suggested email",
               "score": "Score"}


def posted_since(lead: dict) -> str:
    since = month_year(lead["first_seen_at"])
    n = int(lead.get("posting_count_90d") or 1)
    return f"{n} times since {since}" if n > 1 else f"since {since}"


def suggested_email(template: dict | None, lead: dict, contact: dict | None, niche: str, city: str) -> str:
    if not template:
        return ""
    first = (contact or {}).get("contact_first_name") or ""
    body = template["body"]
    if not first:
        body = body.replace("Hi {first_name},", "Hello,", 1)
    d = render({"subject": None, "body": body},
               {"first_name": first, "company": lead["employer_name"], "hm_role": lead["title"],
                "posted_since": posted_since(lead), "open_since": month_year(lead["first_seen_at"]),
                "niche": niche or "", "city": city or lead.get("location_name") or ""})
    return d.body


def lead_rows(store, leads: list[dict], today: dt.date, default_search: dict | None = None) -> list[dict[str, Any]]:
    contacts = store.lead_contacts_for([l["employer_norm"] for l in leads])
    history = store.lead_history([l.get("job_ids") or [] for l in leads])
    template = store.template_by_channel("client_hm_email")
    rows = []
    for l in leads:
        search = {"label": l.get("search_label"), "location": l.get("search_location")} if l.get("search_label") \
            else default_search
        niche, city = niche_and_city(search)
        jobs = sorted((history[j] for j in (l.get("job_ids") or []) if j in history),
                      key=lambda h: h["first_seen_at"], reverse=True)
        latest = l.get("latest_url") or (jobs[0]["url"] if jobs else None)
        previous = next((h["url"] for h in jobs if h["url"] and h["url"] != latest), "") or l.get("old_ad_url") or ""
        salaries = [h["max_salary"] or h["min_salary"] for h in jobs if (h["max_salary"] or h["min_salary"])]
        c = contacts.get(l["employer_norm"])
        rows.append({
            "lead_key": l["lead_key"], "date_added": today.isoformat(), "company": l["employer_name"],
            "role": l["title"], "max_salary": int(max(salaries)) if salaries else "",
            "times_posted": l["posting_count_90d"], "first_seen": l["first_seen_at"].date().isoformat(),
            "location": l.get("location_name") or "", "ad_link": latest or "", "previous_ad_link": previous,
            "decision_maker": (c or {}).get("contact_name") or "", "decision_maker_role": (c or {}).get("contact_role") or "",
            "email": email_for_export(c), "suggested_email": suggested_email(template, l, c, niche, city),
            "score": l["score"],
        })
    return rows
