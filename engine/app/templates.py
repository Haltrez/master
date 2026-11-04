"""Email/DM draft rendering with {variable} placeholders.

If any variable a template uses is empty, the draft carries a warning and the
export skips that row. Do-not-contact agencies/contacts and suppressed emails
never get a draft.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from typing import Any

VARIABLES = ("first_name", "agency", "niche", "city", "lead1_employer", "lead1_role", "lead1_count", "lead1_since",
             "lead2_employer", "lead2_role", "lead2_count", "lead3_employer", "lead3_role", "lead3_count",
             "hm_role", "open_since", "sender_name")
PLACEHOLDER = re.compile(r"\{([a-z0-9_]+)\}")


@dataclass
class Draft:
    subject: str | None
    body: str
    missing: list[str] = field(default_factory=list)
    blocked: str | None = None
    context: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.missing and not self.blocked


def used_variables(*texts: str | None) -> list[str]:
    seen: list[str] = []
    for t in texts:
        for m in PLACEHOLDER.finditer(t or ""):
            if m.group(1) not in seen:
                seen.append(m.group(1))
    return seen


def render(template: dict, ctx: dict[str, Any]) -> Draft:
    missing = [v for v in used_variables(template.get("subject"), template.get("body")) if ctx.get(v) in (None, "")]

    def sub(text: str | None) -> str | None:
        if text is None:
            return None
        return PLACEHOLDER.sub(lambda m: str(ctx.get(m.group(1))) if ctx.get(m.group(1)) not in (None, "")
                               else m.group(0), text)

    return Draft(subject=sub(template.get("subject")), body=sub(template.get("body")) or "", missing=missing,
                 context=ctx)


def month_name(d: Any) -> str:
    if not d:
        return ""
    if isinstance(d, str):
        d = dt.date.fromisoformat(d[:10])
    if isinstance(d, dt.datetime):
        d = d.date()
    return d.strftime("%B")


def month_year(d: Any) -> str:
    if not d:
        return ""
    if isinstance(d, str):
        d = dt.date.fromisoformat(d[:10])
    return d.strftime("%B %Y")


def niche_and_city(search: dict | None) -> tuple[str, str]:
    if not search:
        return "", ""
    label = search.get("label") or ""
    niche = label.split(" - ")[0].strip() if " - " in label else label
    return niche, search.get("location") or ""


def lead_vars(leads: list[dict]) -> dict[str, Any]:
    ctx: dict[str, Any] = {}
    for i, lead in enumerate(leads[:3], 1):
        ctx[f"lead{i}_employer"] = lead["employer_name"]
        ctx[f"lead{i}_role"] = lead["title"]
        ctx[f"lead{i}_count"] = lead["posting_count_90d"]
        ctx[f"lead{i}_since"] = month_name(lead["first_seen_at"])
    return ctx


def agency_context(store, agency_id: int, sender_name: str, contact_id: int | None = None) -> tuple[dict, str | None]:
    """Variables for an agency draft and a reason the draft is blocked (or None)."""
    ag = store.get_agency(agency_id)
    if ag is None:
        raise ValueError("agency not found")
    if ag["stage"] == "do_not_contact":
        return {}, "agency is marked do not contact"
    contacts = [c for c in store.contacts(agency_id) if not c["do_not_contact"]
                and not (c.get("email") and store.is_suppressed(c["email"]))]
    contact = next((c for c in contacts if c["id"] == contact_id), None) if contact_id else (contacts[0] if contacts else None)
    if contact_id and contact is None:
        return {}, "contact is do not contact or suppressed"
    sids = store.agency_search_ids(agency_id)
    search = store.search_row(sids[0]) if sids else None
    niche, city = niche_and_city(search)
    leads = store.top_hot_leads_for_search(search["id"], 3) if search else []
    ctx = {"first_name": (contact or {}).get("first_name") or "", "agency": ag["name"], "niche": niche, "city": city,
           "sender_name": sender_name, "contact_id": (contact or {}).get("id"), "contact_email": (contact or {}).get("email"),
           "search_id": (search or {}).get("id"), "leads": leads, **lead_vars(leads)}
    return ctx, None


def draft_for_agency(store, agency_id: int, template: dict, sender_name: str, contact_id: int | None = None) -> Draft:
    ctx, blocked = agency_context(store, agency_id, sender_name, contact_id)
    if blocked:
        return Draft(subject=None, body="", blocked=blocked)
    return render(template, ctx)


def opener_for_lead(template: dict | None, lead: dict, niche: str, city: str) -> str:
    """The client's own opener to the hiring company (client_hm_email)."""
    if not template:
        return ""
    d = render(template, {"hm_role": lead["title"], "open_since": month_year(lead["first_seen_at"]),
                          "niche": niche, "city": city})
    return d.body
