"""Repost Radar engine API (FastAPI).

Every endpoint needs the header X-API-Key = ENGINE_API_SECRET.
Long work returns 202 + an id at once and continues in the background.
Times are ISO 8601 in UTC; "today" (scorecard, digest, follow-ups) means the
UK day (Europe/London).

Run: uvicorn app.main:app --host 0.0.0.0 --port 8000   (from engine/)
"""

from __future__ import annotations

import datetime as dt
import hmac
import json
import logging
from dataclasses import dataclass
from typing import Any, Callable
from zoneinfo import ZoneInfo

import httpx
from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from pydantic import BaseModel, Field

from .agencies import compliance_label, email_allowed
from .config import Settings, get_settings, setup_logging
from .exports import OUTREACH_COLUMNS, outreach_rows, to_csv
from .pg import AGENCY_STAGES, PgStore, TerritoryTaken
from .templates import draft_for_agency, niche_and_city, opener_for_lead, render

log = logging.getLogger("api")
UK = ZoneInfo("Europe/London")


def sheet_id(url: str | None) -> str | None:
    """https://docs.google.com/spreadsheets/d/<ID>/edit -> <ID> (what Make.com's Sheets modules want)."""
    import re
    m = re.search(r"/spreadsheets/d/([A-Za-z0-9_-]+)", url or "")
    return m.group(1) if m else None


def uk_today() -> dt.date:
    return dt.datetime.now(UK).date()


# ---------------------------------------------------------------------------
# Pluggable work (tests swap these for fakes)
# ---------------------------------------------------------------------------
@dataclass
class Hooks:
    collect_one: Callable[[PgStore, Settings, int, int], Any]
    collect_all: Callable[[PgStore, Settings, dict[int, int]], Any]
    enrich: Callable[[PgStore, Settings, int], dict]
    role_services: Callable[[PgStore, Settings, bool], Any]
    post_webhook: Callable[[str, dict], None]
    make_sheets: Callable[[PgStore, Settings], Any] | None = None     # None -> the owner's Google account
    make_claude: Callable[[Settings], Any] | None = None              # None -> anthropic.Anthropic()
    make_tools: Callable[[PgStore, Settings], Any] | None = None      # None -> pipeline.make_tools
    make_research: Callable[[PgStore, Settings], Any] | None = None   # None -> research_tools.Research
    make_smartlead: Callable[[Settings], Any] | None = None           # None -> smartlead.Smartlead
    run_in_thread: bool = True                                        # tests run projects inline


def default_hooks() -> Hooks:
    from .pipeline import collect_all, collect_one

    def enrich(store, settings, agency_id):
        from .agencies import enrich_agency
        from .ch_client import CachedCompaniesHouse, CompaniesHouseClient
        from .lead_contacts import hunter_for
        from .web_lookup import Brave
        ch = CachedCompaniesHouse(CompaniesHouseClient(settings.companies_house_api_key), store)
        return enrich_agency(store, agency_id, ch,
                             brave=Brave(settings.brave_api_key) if settings.brave_api_key else None,
                             hunter=hunter_for(store, settings))

    def role_services(store, settings, need_fetcher):
        from .geo import Geocoder
        from .pipeline import make_reed
        from .polite import PoliteFetcher
        from .role_match import Services
        from .wayback import Wayback
        return Services(repo=store, settings=settings, geocoder=Geocoder(store),
                        fetcher=PoliteFetcher() if need_fetcher else None, wayback=Wayback(store),
                        reed=make_reed(settings))

    def post_webhook(url, payload):
        try:
            httpx.post(url, json=payload, timeout=15)
        except httpx.HTTPError as exc:
            log.warning("webhook %s failed: %s", url, type(exc).__name__)

    return Hooks(collect_one=lambda s, st, sid, rid: collect_one(s, st, sid, rid),
                 collect_all=lambda s, st, rids: collect_all(s, st, rids),
                 enrich=enrich, role_services=role_services, post_webhook=post_webhook)


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------
class SearchIn(BaseModel):
    label: str
    keywords: list[str]
    location: str
    radius_miles: int = 15
    min_salary: int | None = None
    active: bool = True


class SearchPatch(BaseModel):
    label: str | None = None
    keywords: list[str] | None = None
    location: str | None = None
    radius_miles: int | None = None
    min_salary: int | None = None
    active: bool | None = None


class StatusIn(BaseModel):
    status: str


class AgencyPatch(BaseModel):
    stage: str | None = None
    notes: str | None = None
    website: str | None = None
    next_follow_up: dt.date | None = None
    clear_follow_up: bool = False
    log_activity_type: str | None = None   # "How did you contact them?" answer when moving to contacted


class ContactIn(BaseModel):
    full_name: str
    first_name: str | None = None
    role: str | None = None
    email: str | None = None
    email_verified: bool = False
    linkedin_url: str | None = None
    do_not_contact: bool = False


class ContactPatch(BaseModel):
    full_name: str | None = None
    first_name: str | None = None
    role: str | None = None
    email: str | None = None
    email_verified: bool | None = None
    linkedin_url: str | None = None
    do_not_contact: bool | None = None


class ActivityIn(BaseModel):
    agency_id: int
    type: str
    contact_id: int | None = None
    notes: str | None = None
    happened_at: dt.datetime | None = None


class TemplateIn(BaseModel):
    name: str
    channel: str
    subject: str | None = None
    body: str


class PreviewIn(BaseModel):
    subject: str | None = None
    body: str
    agency_id: int | None = None


class ClientIn(BaseModel):
    agency_id: int
    plan: str = "pilot"
    monthly_price_gbp: float = 0
    start_date: dt.date = Field(default_factory=uk_today)
    end_date: dt.date | None = None
    sheet_url: str | None = None
    delivery_email: str | None = None
    search_ids: list[int] = []


class ClientPatch(BaseModel):
    plan: str | None = None
    monthly_price_gbp: float | None = None
    end_date: dt.date | None = None
    sheet_url: str | None = None
    delivery_email: str | None = None
    status: str | None = None


class TerritoriesIn(BaseModel):
    search_ids: list[int]


class SheetIn(BaseModel):
    sheet_url: str


class OutcomeIn(BaseModel):
    outcome: str


class BriefIn(BaseModel):
    agency_id: int | None = None
    client_id: int | None = None
    role: str
    location: str
    radius_miles: int = 30
    sector_keywords: list[str] = []
    seniority: str | None = None


class BriefPatch(BaseModel):
    location: str | None = None
    radius_miles: int | None = None
    sector_keywords: list[str] | None = None
    seniority: str | None = None
    active: bool | None = None


class MatchStatusIn(BaseModel):
    ids: list[int]
    status: str


class EmployerIn(BaseModel):
    name: str
    website: str | None = None
    careers_url: str | None = None
    town: str | None = None


class EmployerPatch(BaseModel):
    name: str | None = None
    website: str | None = None
    careers_url: str | None = None
    town: str | None = None
    status: str | None = None
    check_every_days: int | None = None


class FamilyPatch(BaseModel):
    name: str | None = None
    exact_titles: list[str] | None = None
    related_titles: list[str] | None = None


class FamilyIn(BaseModel):
    name: str
    exact_titles: list[str]
    related_titles: list[str] = []


class AgencyIn(BaseModel):
    name: str
    website: str | None = None


class ProspectsIn(BaseModel):
    csv: str
    trust_unlabelled: bool = True   # no email-status column: Smartlead Prospect only reveals checked emails


class CrmListIn(BaseModel):
    csv: str
    mode: str | None = None      # exclude | flag


class CrmModeIn(BaseModel):
    mode: str


class OrderIn(BaseModel):
    crm_csv: str | None = None   # the agency's CRM export, checked before the first leads go out
    crm_mode: str | None = None
    agency_name: str
    industry: str
    location: str
    leads_wanted: int = 20
    frequency: str = "once"
    package: str = "sheet"
    client_email: str | None = None
    keywords: list[str] | None = None
    radius_miles: int = 20


class OrderPatch(BaseModel):
    leads_wanted: int | None = None
    frequency: str | None = None
    client_email: str | None = None
    status: str | None = None
    package: str | None = None


class TerritoryIn(BaseModel):
    industry: str
    location: str
    radius_miles: int = 20
    keywords: list[str] | None = None


class GoogleExchangeIn(BaseModel):
    code: str
    redirect_uri: str


class PushIn(BaseModel):
    ids: list[int]


class RowActionIn(BaseModel):
    action: str                  # remove | swap | leaders | sales
    which: int = 1               # swap: lead 1 or 2


class CategoryIn(BaseModel):
    category: str


class ProjectIn(BaseModel):
    brief: str
    title: str | None = None


class ProjectMessageIn(BaseModel):
    message: str = ""


class ChatIn(BaseModel):
    message: str
    conversation_id: str | None = None


class LeadContactPatch(BaseModel):
    contact_name: str | None = None
    contact_first_name: str | None = None
    contact_role: str | None = None
    email: str | None = None
    email_verified: bool | None = None


MATCH_STATUSES = ("new", "sent", "contacted", "interview", "placed", "not_relevant")
DELIVERY_OUTCOMES = ("none", "contacted", "meeting", "placement")
CLIENT_PLANS = ("pilot", "founding", "done_for_you")


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
VERSION = "2026-11-07.1"   # shown in the CRM footer, so it's visible when an update is live


def create_app(settings: Settings | None = None, store: PgStore | None = None, hooks: Hooks | None = None) -> FastAPI:
    settings = settings or get_settings()
    hooks = hooks or default_hooks()
    docs = settings.enable_docs
    app = FastAPI(title="Repost Radar engine", version="1.0",
                  description="All times UTC ISO 8601; 'today' is the UK day (Europe/London).",
                  docs_url="/docs" if docs else None, redoc_url=None, openapi_url="/openapi.json" if docs else None)
    state: dict[str, PgStore] = {}

    def db() -> PgStore:
        if store is not None:
            return store
        if "store" not in state:
            state["store"] = PgStore(settings.database_url)
        return state["store"]

    def auth(x_api_key: str | None = Header(default=None)) -> None:
        secret = settings.engine_api_secret
        if not secret or not x_api_key or not hmac.compare_digest(x_api_key, secret):
            raise HTTPException(status_code=401, detail="missing or wrong X-API-Key")

    A = [Depends(auth)]

    def strict(s: PgStore) -> bool:
        v = s.setting("verified_companies_only", True)
        return True if v is None else bool(v)

    def only_verified(s: PgStore, leads: list[dict]) -> list[dict]:
        ok = s.verified_employer_norms([l["employer_norm"] for l in leads])
        return [l for l in leads if l["employer_norm"] in ok]

    def sender(s: PgStore) -> str:
        return str(s.setting("sender_name", "Roberts") or "Roberts")

    def notfound(what: str):
        raise HTTPException(404, f"{what} not found")

    @app.exception_handler(TerritoryTaken)
    async def _taken(request: Request, exc: TerritoryTaken):
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    # -- health ------------------------------------------------------------------
    @app.get("/health", dependencies=A)
    def health():
        try:
            ok = db().ping()
        except Exception as exc:
            return JSONResponse(status_code=503, content={"ok": False, "db": False, "error": type(exc).__name__})
        return {"ok": ok, "db": ok, "time": dt.datetime.now(dt.timezone.utc), "version": VERSION}

    # -- searches + runs ------------------------------------------------------------
    @app.get("/searches", dependencies=A)
    def searches():
        s = db()
        per = s.listings_by_source()
        return [{**x, "sources": per.get(x["id"], {})} for x in s.searches_overview()]

    @app.post("/searches", dependencies=A, status_code=201)
    def create_search(body: SearchIn):
        s = db().create_search(body.label, [k.strip() for k in body.keywords if k.strip()], body.location,
                               body.radius_miles, body.min_salary)
        if not body.active:
            db().update_search(s.id, {"active": False})
        return db().search_row(s.id)

    @app.patch("/searches/{search_id}", dependencies=A)
    def patch_search(search_id: int, body: SearchPatch):
        r = db().update_search(search_id, body.model_dump(exclude_none=True))
        return r or notfound("search")

    @app.post("/searches/{search_id}/run", dependencies=A, status_code=202)
    def run_one(search_id: int, bg: BackgroundTasks):
        s = db()
        if not s.search_row(search_id):
            notfound("search")
        run_id = s.create_run(search_id)
        bg.add_task(hooks.collect_one, s, settings, search_id, run_id)
        return {"run_id": run_id, "status": "running"}

    @app.post("/run-all", dependencies=A, status_code=202)
    def run_all(bg: BackgroundTasks):
        s = db()
        run_ids = {x.id: s.create_run(x.id) for x in s.list_active_searches()}
        bg.add_task(hooks.collect_all, s, settings, run_ids)
        return {"run_ids": list(run_ids.values()), "searches": list(run_ids), "status": "running"}

    @app.get("/runs/{run_id}", dependencies=A)
    def get_run(run_id: int):
        return db().get_run(run_id) or notfound("run")

    @app.get("/tasks/{task_id}", dependencies=A)
    def get_task(task_id: int):
        return db().get_task(task_id) or notfound("task")

    # -- hot leads -------------------------------------------------------------------
    @app.get("/hot-leads", dependencies=A)
    def hot_leads(search_id: int | None = None, min_score: int | None = None, since: dt.datetime | None = None,
                  status: str | None = None, live_only: bool = False, limit: int = 500):
        s = db()
        rows = s.hot_leads(search_id=search_id, min_score=min_score, since=since, status=status,
                           live_only=live_only, limit=limit)
        contacts = s.lead_contacts_for([r["employer_norm"] for r in rows])
        for r in rows:
            c = contacts.get(r["employer_norm"])
            r["contact"] = {"name": c.get("contact_name"), "role": c.get("contact_role"), "email": c.get("email"),
                            "verified": c.get("email_verified"), "checked": bool(c.get("contacts_checked"))} if c else None
            r["company_verified"] = bool(c and c.get("verified_company") and not c.get("is_agency"))
            r["company_check"] = None if not c else ("verified" if r["company_verified"] else
                                                      "agency" if c.get("is_agency") else c.get("ch_company_type") or "unverified")
        return rows

    @app.patch("/hot-leads/{lead_key}", dependencies=A)
    def patch_hot_lead(lead_key: str, body: StatusIn):
        if body.status not in ("new", "used", "ignored"):
            raise HTTPException(422, "status must be new, used or ignored")
        return db().set_hot_lead_status(lead_key, body.status) or notfound("hot lead")

    # -- agencies ----------------------------------------------------------------------
    def agency_out(a: dict) -> dict:
        return {**a, "email_allowed": email_allowed(a), "compliance": compliance_label(a)}

    @app.get("/agencies", dependencies=A)
    def agencies(stage: str | None = None, search_id: int | None = None, q: str | None = None):
        return [agency_out(a) for a in db().agencies_list(stage=stage, search_id=search_id, q=q)]

    @app.post("/agencies", dependencies=A, status_code=201)
    def create_agency(body: AgencyIn):
        s = db()
        a = s.create_agency(body.name.strip())
        if body.website and not a.get("website"):
            a = s.update_agency(a["id"], {"website": body.website})
        return agency_out(a)

    @app.get("/agencies/{agency_id}", dependencies=A)
    def agency(agency_id: int):
        s = db()
        a = s.get_agency(agency_id) or notfound("agency")
        sids = s.agency_search_ids(agency_id)
        search = s.search_row(sids[0]) if sids else None
        briefs = s.briefs(agency_id)
        return {
            "agency": agency_out(a),
            "contacts": s.contacts(agency_id),
            "live_ads": s.agency_live_ads(agency_id),
            "top_hot_leads": s.top_hot_leads_for_search(search["id"], 3) if search else [],
            "niche_search": search,
            "activities": s.activities(agency_id),
            "clients": [c for c in s.clients() if c["agency_id"] == agency_id],
            "briefs": briefs,
            "matches": s.matches_detail([b["id"] for b in briefs]),
        }

    @app.patch("/agencies/{agency_id}", dependencies=A)
    def patch_agency(agency_id: int, body: AgencyPatch):
        s = db()
        a = s.get_agency(agency_id) or notfound("agency")
        fields = body.model_dump(exclude_none=True, exclude={"log_activity_type", "clear_follow_up"})
        if body.clear_follow_up:
            fields["next_follow_up"] = None
        if "stage" in fields and fields["stage"] not in AGENCY_STAGES:
            raise HTTPException(422, f"stage must be one of {AGENCY_STAGES}")
        if fields.get("stage") == "contacted" and a["stage"] != "contacted":
            if body.log_activity_type:
                s.add_activity({"agency_id": agency_id, "type": body.log_activity_type, "notes": "stage -> contacted"})
            elif not s.has_outreach_activity(agency_id):
                return JSONResponse(status_code=409, content={"detail": "needs_activity",
                                                              "message": "How did you contact them?"})
        if fields.get("stage") == "do_not_contact":
            # Forever: their emails go on the suppression list too.
            for c in s.contacts(agency_id):
                if c.get("email"):
                    s.suppress(c["email"], "agency do not contact")
            s.q("update contacts set do_not_contact = true where agency_id = %s", (agency_id,))
        return agency_out(s.update_agency(agency_id, fields))

    @app.post("/agencies/{agency_id}/enrich", dependencies=A, status_code=202)
    def enrich(agency_id: int, bg: BackgroundTasks):
        s = db()
        s.get_agency(agency_id) or notfound("agency")
        if not settings.companies_house_api_key:
            raise HTTPException(400, "COMPANIES_HOUSE_API_KEY is not set")
        task_id = s.create_task("enrich", agency_id)

        def work():
            try:
                s.finish_task(task_id, "ok", hooks.enrich(s, settings, agency_id))
            except Exception as exc:
                log.exception("enrich %d failed", agency_id)
                s.finish_task(task_id, "error", error=str(exc))
        bg.add_task(work)
        return {"task_id": task_id, "status": "running"}

    @app.post("/agencies/{agency_id}/draft", dependencies=A)
    def draft(agency_id: int, template_id: int = Query(...), contact_id: int | None = None):
        s = db()
        t = s.template(template_id) or notfound("template")
        s.get_agency(agency_id) or notfound("agency")
        d = draft_for_agency(s, agency_id, t, sender(s), contact_id)
        return {"subject": d.subject, "body": d.body, "missing": d.missing, "blocked": d.blocked, "ok": d.ok,
                "contact_id": d.context.get("contact_id"), "contact_email": d.context.get("contact_email"),
                "template": {"id": t["id"], "name": t["name"], "channel": t["channel"]}}

    @app.post("/agencies/{agency_id}/contacts", dependencies=A, status_code=201)
    def add_contact(agency_id: int, body: ContactIn):
        s = db()
        s.get_agency(agency_id) or notfound("agency")
        row = {**body.model_dump(), "agency_id": agency_id, "source": "manual"}
        if not row.get("first_name"):
            row["first_name"] = body.full_name.split()[0]
        c = s.add_contact(row)
        if c is None:
            raise HTTPException(409, "that person is already on this agency")
        return c

    @app.patch("/contacts/{contact_id}", dependencies=A)
    def patch_contact(contact_id: int, body: ContactPatch):
        s = db()
        c = s.contact(contact_id) or notfound("contact")
        fields = body.model_dump(exclude_none=True)
        if fields.get("do_not_contact") and (fields.get("email") or c.get("email")):
            s.suppress(fields.get("email") or c["email"], "contact do not contact")
            fields.pop("email", None)
            return s._update("contacts", contact_id, fields)
        try:
            return s.update_contact(contact_id, fields)
        except ValueError as exc:
            raise HTTPException(409, str(exc))

    @app.post("/contacts/{contact_id}/forget", dependencies=A)
    def forget(contact_id: int):
        if not db().forget_contact(contact_id):
            notfound("contact")
        return {"forgotten": True}

    # -- activities ------------------------------------------------------------------------
    @app.post("/activities", dependencies=A, status_code=201)
    def add_activity(body: ActivityIn):
        try:
            return db().add_activity(body.model_dump(exclude_none=True))
        except ValueError as exc:
            raise HTTPException(422, str(exc))

    @app.get("/activities", dependencies=A)
    def activities(agency_id: int | None = None, limit: int = 200):
        return db().activities(agency_id, limit)

    # -- templates ---------------------------------------------------------------------------
    @app.get("/templates", dependencies=A)
    def templates():
        return db().templates()

    @app.post("/templates", dependencies=A, status_code=201)
    def create_template(body: TemplateIn):
        if body.channel not in ("email", "linkedin", "client_hm_email"):
            raise HTTPException(422, "channel must be email, linkedin or client_hm_email")
        return db().save_template(body.model_dump())

    @app.patch("/templates/{template_id}", dependencies=A)
    def patch_template(template_id: int, body: TemplateIn):
        db().template(template_id) or notfound("template")
        return db().save_template(body.model_dump(), template_id)

    @app.delete("/templates/{template_id}", dependencies=A, status_code=204)
    def delete_template(template_id: int):
        db().delete_template(template_id)
        return Response(status_code=204)

    @app.post("/templates/preview", dependencies=A)
    def preview(body: PreviewIn):
        s = db()
        t = {"subject": body.subject, "body": body.body}
        if body.agency_id:
            d = draft_for_agency(s, body.agency_id, t, sender(s))
        else:
            d = render(t, {"sender_name": sender(s)})
        return {"subject": d.subject, "body": d.body, "missing": d.missing, "blocked": d.blocked}

    # -- clients + delivery -----------------------------------------------------------------
    @app.get("/clients", dependencies=A)
    def clients():
        return db().clients()

    @app.get("/clients/active", dependencies=A)
    def clients_active():
        return [{"id": c["id"], "agency": c["agency_name"], "plan": c["plan"], "sheet_url": c["sheet_url"],
                 "sheet_id": sheet_id(c["sheet_url"]),
                 "delivery_email": c["delivery_email"], "territories": c["territories"]}
                for c in db().clients(active_only=True)]

    @app.post("/clients", dependencies=A, status_code=201)
    def create_client(body: ClientIn, bg: BackgroundTasks):
        if body.plan not in CLIENT_PLANS:
            raise HTTPException(422, f"plan must be one of {CLIENT_PLANS}")
        s = db()
        a = s.get_agency(body.agency_id) or notfound("agency")
        c = s.create_client(body.model_dump(exclude={"search_ids"}), body.search_ids)
        hook = (s.setting("make_webhooks", {}) or {}).get("new_client")
        if hook:
            bg.add_task(hooks.post_webhook, hook, {"client_id": c["id"], "agency": a["name"],
                                                   "delivery_email": c["delivery_email"]})
        return c

    @app.patch("/clients/{client_id}", dependencies=A)
    def patch_client(client_id: int, body: ClientPatch):
        db().client(client_id) or notfound("client")
        return db().update_client(client_id, body.model_dump(exclude_none=True))

    @app.post("/clients/{client_id}/convert", dependencies=A)
    def convert(client_id: int, plan: str = "done_for_you", monthly_price_gbp: float = 1250):
        s = db()
        c = s.client(client_id) or notfound("client")
        out = s.update_client(client_id, {"plan": plan, "monthly_price_gbp": monthly_price_gbp})
        if c.get("agency_id"):
            s.update_agency(c["agency_id"], {"stage": "paid"})
        return out

    @app.put("/clients/{client_id}/territories", dependencies=A)
    def territories(client_id: int, body: TerritoriesIn):
        db().client(client_id) or notfound("client")
        db().set_territories(client_id, body.search_ids)
        return db().client(client_id)

    @app.patch("/clients/{client_id}/sheet", dependencies=A)
    def set_sheet(client_id: int, body: SheetIn):
        db().client(client_id) or notfound("client")
        return db().update_client(client_id, {"sheet_url": body.sheet_url})

    @app.get("/clients/{client_id}/deliveries", dependencies=A)
    def deliveries(client_id: int):
        return db().deliveries(client_id)

    @app.patch("/deliveries/{delivery_id}", dependencies=A)
    def delivery_outcome(delivery_id: int, body: OutcomeIn):
        if body.outcome not in DELIVERY_OUTCOMES:
            raise HTTPException(422, f"outcome must be one of {DELIVERY_OUTCOMES}")
        return db().set_delivery_outcome(delivery_id, body.outcome) or notfound("delivery")

    @app.get("/clients/{client_id}/daily-leads", dependencies=A)
    def daily_leads(client_id: int, mark: bool = False, limit: int = 100):
        s = db()
        c = s.client(client_id) or notfound("client")
        leads = s.undelivered_leads(client_id, limit)
        if strict(s):
            leads = only_verified(s, leads)   # unverified ones wait (not marked) until Companies House confirms them
        if mark and leads:
            newly = set(s.mark_delivered(client_id, [l["lead_key"] for l in leads]))
            leads = [l for l in leads if l["lead_key"] in newly]   # never the same lead twice, even if called twice at once
        from .leads import lead_rows
        out = lead_rows(s, sorted(leads, key=lambda x: -x["score"]), uk_today())
        for r in out:
            r["suggested_opener"] = r["suggested_email"]   # older Make scenarios used this name
        return {"client_id": client_id, "agency": c["agency_name"], "sheet_url": c["sheet_url"],
                "sheet_id": sheet_id(c["sheet_url"]),
                "delivery_email": c["delivery_email"], "count": len(out), "leads": out}

    # -- exports ---------------------------------------------------------------------------------
    @app.get("/exports/outreach.csv", dependencies=A)
    def outreach_csv(search_id: int | None = None, stage: str = "new"):
        rows, skipped = outreach_rows(db(), search_id=search_id, stage=stage)
        return Response(content=to_csv(OUTREACH_COLUMNS, rows), media_type="text/csv",
                        headers={"X-Skipped": json.dumps(dict(skipped)), "X-Skipped-Total": str(sum(skipped.values())),
                                 "X-Exported": str(len(rows)),
                                 "Content-Disposition": "attachment; filename=outreach.csv"})

    @app.get("/exports/hot-leads.csv", dependencies=A)
    def hot_leads_csv(search_id: int | None = None, min_score: int | None = None, status: str | None = None,
                      live_only: bool = False):
        from .leads import CSV_HEADERS, LEAD_COLUMNS, lead_rows
        s = db()
        search = s.search_row(search_id) if search_id else None
        leads = s.hot_leads(search_id=search_id, min_score=min_score, status=status, live_only=live_only)
        if strict(s):
            leads = only_verified(s, leads)
        rows = lead_rows(s, leads, uk_today(), search)
        csv_text = to_csv([CSV_HEADERS[c] for c in LEAD_COLUMNS], [{CSV_HEADERS[k]: r[k] for k in LEAD_COLUMNS} for r in rows])
        return Response(content="\ufeff" + csv_text, media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f"attachment; filename=hot-leads-{uk_today()}.csv"})

    @app.post("/hot-leads/{lead_key}/contact", dependencies=A, status_code=202)
    def find_lead_contact(lead_key: str, bg: BackgroundTasks):
        s = db()
        lead = s.one("select * from hot_leads where lead_key = %s", (lead_key,)) or notfound("hot lead")
        if not settings.companies_house_api_key:
            raise HTTPException(400, "COMPANIES_HOUSE_API_KEY is not set")
        task_id = s.create_task("lead_contact")

        def work():
            from .ch_client import CachedCompaniesHouse, CompaniesHouseClient
            from .lead_contacts import enrich_lead_company
            from .lead_contacts import hunter_for
            from .web_lookup import Brave
            try:
                r = enrich_lead_company(s, lead["employer_name"], lead["employer_norm"],
                                        CachedCompaniesHouse(CompaniesHouseClient(settings.companies_house_api_key), s),
                                        Brave(settings.brave_api_key) if settings.brave_api_key else None,
                                        hunter_for(s, settings))
                s.finish_task(task_id, "ok", {k: r.get(k) for k in ("contact_name", "contact_role", "email")})
            except Exception as exc:
                s.finish_task(task_id, "error", error=str(exc))
        bg.add_task(work)
        return {"task_id": task_id, "status": "running"}

    @app.patch("/lead-contacts/{employer_norm}", dependencies=A)
    def patch_lead_contact(employer_norm: str, body: LeadContactPatch):
        try:
            return db().update_lead_contact(employer_norm, body.model_dump(exclude_none=True))
        except ValueError as exc:
            raise HTTPException(409, str(exc))

    @app.get("/exports/matches.csv", dependencies=A)
    def matches_csv(agency_id: int):
        from .role_match import CSV_COLUMNS, SOURCE_LABELS
        s = db()
        s.get_agency(agency_id) or notfound("agency")
        briefs = s.briefs(agency_id)
        rows = []
        for m in s.matches_detail([b["id"] for b in briefs]):
            if m["status"] == "not_relevant":
                continue
            rows.append({"candidate_role": m["brief_label"], "company": m["company"], "their_open_role": m["their_role"],
                         "location": m["location_label"] or m["location_text"] or m["town"] or "",
                         "distance_miles": f"{m['distance_miles']:.0f}" if m["distance_miles"] is not None else "unknown",
                         "open_since": m["open_since_label"] or "unknown", "signal": m["signal"] or "",
                         "link": (m["url"] or "").split("#")[0], "source": SOURCE_LABELS.get(m["source"], m["source"]),
                         "score": m["score"]})
        csv_text = to_csv(CSV_COLUMNS, rows) + "\n"
        for b in sorted(briefs, key=lambda x: x["label"].lower()):
            d = s.cache_get(f"demand:{b['id']}") or {}
            csv_text += to_csv(CSV_COLUMNS, [{"candidate_role": b["label"],
                                              "signal": d.get("sentence", "Market demand: not checked yet")}]).split("\n", 1)[1]
        return Response(content=csv_text, media_type="text/csv",
                        headers={"Content-Disposition": "attachment; filename=matches.csv"})

    # -- today + digest ------------------------------------------------------------------------------
    def target(s: PgStore) -> int:
        try:
            return int(s.setting("daily_outreach_target", 100))
        except (TypeError, ValueError):
            return 100

    def do_next(s: PgStore, limit: int = 15) -> list[dict]:
        out = []
        for a in s.agencies_list(stage="new"):
            if not email_allowed(a) or not a.get("director_name"):
                continue
            out.append(agency_out(a))
            if len(out) >= limit:
                break
        return out

    @app.get("/today", dependencies=A)
    def today():
        s = db()
        d = uk_today()
        sc = s.scorecard(d, d - dt.timedelta(days=d.weekday()))
        return {"date": d, "target": target(s), "scorecard": sc, "history": s.daily_history(14),
                "do_next": do_next(s), "follow_ups": [agency_out(a) for a in s.follow_ups_due(d)],
                "replies": [agency_out(a) for a in s.replies_waiting()], "new_matches": s.new_matches_by_agency(),
                "new_hot_leads": s.q("select count(*) as n from hot_leads where created_at >= now() - interval '1 day'")[0]["n"]}

    @app.get("/digest/today", dependencies=A)
    def digest():
        s = db()
        d = uk_today()
        sc = s.scorecard(d, d - dt.timedelta(days=d.weekday()))
        new_hot = s.q("select * from hot_leads where created_at >= now() - interval '1 day' and status <> 'ignored' "
                      "order by score desc limit 20")
        follow = s.follow_ups_due(d)
        agencies_next = do_next(s, 10)
        t = target(s)
        lines = [f"Repost Radar, {d:%A %-d %B}",
                 f"Yesterday: {sc['yesterday']} outreach actions (target {t}).",
                 f"New hot leads: {len(new_hot)}"]
        lines += [f"  - {h['employer_name']}: {h['title']} ({h['posting_count_90d']}x, score {h['score']})"
                  for h in new_hot[:10]]
        lines.append(f"Agencies to contact today: {len(agencies_next)}")
        lines += [f"  - {a['name']} ({a['director_name']})" for a in agencies_next]
        lines.append(f"Follow-ups due: {len(follow)}")
        lines += [f"  - {a['name']} (due {a['next_follow_up']:%-d %b})" for a in follow[:20]]
        return {"date": d, "yesterday": {"outreach_actions": sc["yesterday"], "target": t},
                "new_hot_leads": new_hot, "agencies_to_contact": agencies_next, "follow_ups_due": follow,
                "text": "\n".join(lines)}

    # -- global search + settings -------------------------------------------------------------------
    @app.get("/search", dependencies=A)
    def global_search(q: str = Query(..., min_length=2)):
        return db().global_search(q)

    @app.get("/settings", dependencies=A)
    def get_settings_ep():
        s = db()
        keys = {"reed": settings.reed_api_key, "adzuna": settings.adzuna_app_id and settings.adzuna_app_key,
                "careerjet": settings.careerjet_api_key, "jooble": settings.jooble_api_key, "indeed": settings.apify_token,
                "theirstack": settings.theirstack_api_key, "companies house": settings.companies_house_api_key,
                "brave": settings.brave_api_key, "hunter": settings.hunter_api_key}
        sources = []
        for name, key in keys.items():
            last = s.cache_get(f"source_status:{name}") if name not in ("companies house", "brave", "hunter") else None
            sources.append({"name": name, "key_set": bool(key), "last": last})
        from .google_sheets import SETTING as GOOGLE_SETTING
        return {"settings": {k: v for k, v in s.settings().items() if k != GOOGLE_SETTING}, "sources": sources,
                "google": {**google_status(s), "client_set": bool(settings.google_client_id and settings.google_client_secret)},
                "assistant": {"key_set": bool(settings.anthropic_api_key), "model": settings.assistant_model},
                "configured": {k: bool(v) for k, v in keys.items()}}

    @app.patch("/settings", dependencies=A)
    def patch_settings(body: dict[str, Any]):
        s = db()
        allowed = {"repost_threshold", "daily_outreach_target", "sender_name", "make_webhooks", "reposts_only",
                   "archive_checks_per_run", "lead_contacts_per_run", "verified_companies_only",
                   "lead_ch_checks_per_run", "hunter_monthly_limit"}
        bad = set(body) - allowed
        if bad:
            raise HTTPException(422, f"unknown settings {sorted(bad)}")
        for k, v in body.items():
            s.set_setting(k, v)
        return s.settings()

    # -- role match ---------------------------------------------------------------------------------
    @app.get("/role-families", dependencies=A)
    def families():
        return db().families()

    @app.post("/role-families", dependencies=A, status_code=201)
    def add_family(body: FamilyIn):
        return db()._insert("role_families", body.model_dump())

    @app.patch("/role-families/{family_id}", dependencies=A)
    def patch_family(family_id: int, body: FamilyPatch):
        return db().update_family(family_id, body.model_dump(exclude_none=True)) or notfound("role family")

    @app.get("/role-families/{family_id}/preview", dependencies=A)
    def family_preview(family_id: int, exact_titles: list[str] | None = Query(None),
                       related_titles: list[str] | None = Query(None)):
        from .roles import match_title
        s = db()
        f = next((x for x in s.families() if x["id"] == family_id), None) or notfound("role family")
        ex, rel = exact_titles or f["exact_titles"], related_titles or f["related_titles"]
        emps = {e["id"]: e["name"] for e in s.employers()}
        out = []
        for j in s.jobs():
            m = match_title(j["title"], ex, rel, f["name"])
            if m:
                out.append({"job_id": j["id"], "title": j["title"], "company": emps.get(j["employer_id"]),
                            "kind": m.kind, "seniority": m.seniority_flag})
        return out

    @app.get("/briefs", dependencies=A)
    def briefs(agency_id: int | None = None, client_id: int | None = None):
        s = db()
        if client_id:
            return s.briefs_for_client(client_id)
        if agency_id:
            return s.briefs(agency_id)
        raise HTTPException(422, "pass agency_id or client_id")

    @app.post("/briefs", dependencies=A, status_code=201)
    def create_brief(body: BriefIn):
        from .role_match import BriefError, find_family
        s = db()
        agency_id = body.agency_id
        if body.client_id:
            c = s.client(body.client_id) or notfound("client")
            agency_id = agency_id or c["agency_id"]
        if not agency_id:
            raise HTTPException(422, "pass agency_id or client_id")
        s.get_agency(agency_id) or notfound("agency")
        try:
            fam = find_family(s, body.role)
        except BriefError as exc:
            raise HTTPException(422, str(exc))
        svc = hooks.role_services(s, settings, False)
        place = svc.geocoder.geocode(body.location) if svc.geocoder else None
        return s.save_brief({"agency_id": agency_id, "client_id": body.client_id, "role_family_id": fam["id"],
                             "label": f"{fam['name']} - {body.location}", "location": body.location,
                             "lat": place.lat if place else None, "lng": place.lng if place else None,
                             "radius_miles": body.radius_miles, "sector_keywords": body.sector_keywords,
                             "seniority": body.seniority, "active": True})

    @app.patch("/briefs/{brief_id}", dependencies=A)
    def patch_brief(brief_id: int, body: BriefPatch):
        s = db()
        s.brief(brief_id) or notfound("brief")
        fields = body.model_dump(exclude_none=True)
        if "location" in fields:
            svc = hooks.role_services(s, settings, False)
            p = svc.geocoder.geocode(fields["location"]) if svc.geocoder else None
            fields.update(lat=p.lat if p else None, lng=p.lng if p else None)
        return s.update_brief(brief_id, fields)

    @app.post("/agencies/{agency_id}/match", dependencies=A, status_code=202)
    def match(agency_id: int, bg: BackgroundTasks, check: bool = True, use_reed: bool = True):
        s = db()
        a = s.get_agency(agency_id) or notfound("agency")
        task_id = s.create_task("match", agency_id)

        def work():
            from .role_match import check_employers, run_match
            try:
                svc = hooks.role_services(s, settings, check)
                checked = [r.__dict__ for r in check_employers(svc)] if check else []
                report = run_match(svc, a["name"], use_reed=use_reed and svc.reed is not None)
                s.finish_task(task_id, "ok", {"matches": len(report.rows),
                                              "employers_checked": sum(1 for c in checked if c["outcome"] == "checked")})
            except Exception as exc:
                log.exception("match for agency %d failed", agency_id)
                s.finish_task(task_id, "error", error=str(exc))
        bg.add_task(work)
        return {"task_id": task_id, "status": "running"}

    @app.get("/agencies/{agency_id}/matches", dependencies=A)
    def matches(agency_id: int, brief_id: int | None = None, min_score: int | None = None, status: str | None = None):
        s = db()
        ids = [brief_id] if brief_id else [b["id"] for b in s.briefs(agency_id)]
        return [{**m, "link": (m["url"] or "").split("#")[0],
                 "location": m["location_label"] or m["location_text"] or m["town"] or ""}
                for m in s.matches_detail(ids, min_score, status)]

    @app.post("/matches/status", dependencies=A)
    def match_status(body: MatchStatusIn):
        if body.status not in MATCH_STATUSES:
            raise HTTPException(422, f"status must be one of {MATCH_STATUSES}")
        return {"updated": db().set_match_status(body.ids, body.status)}

    @app.post("/agencies/{agency_id}/matches/send-to-sheet", dependencies=A, status_code=202)
    def send_to_sheet(agency_id: int, bg: BackgroundTasks, sheet_url: str | None = None):
        s = db()
        s.get_agency(agency_id) or notfound("agency")
        hook = (s.setting("make_webhooks", {}) or {}).get("matches_to_sheet")
        if not hook:
            raise HTTPException(400, "set the 'matches_to_sheet' Make.com webhook URL in Settings first")
        client = next((c for c in s.clients() if c["agency_id"] == agency_id), None)
        url = sheet_url or (client or {}).get("sheet_url")
        if not url:
            raise HTTPException(400, "no Google Sheet for this agency: pass sheet_url or create a client with one")
        bg.add_task(hooks.post_webhook, hook, {"agency_id": agency_id, "sheet_url": url})
        return {"status": "sent to Make.com"}

    @app.get("/employers", dependencies=A)
    def employers(status: str | None = None, town: str | None = None):
        s = db()
        counts: dict[int, int] = {}
        for j in s.jobs():
            counts[j["employer_id"]] = counts.get(j["employer_id"], 0) + 1
        rows = [{**e, "open_roles": counts.get(e["id"], 0)} for e in s.employers()
                if (not status or e["status"] == status) and (not town or (town.lower() in (e.get("town") or "").lower()))]
        return rows

    @app.post("/employers", dependencies=A, status_code=201)
    def add_employer(body: EmployerIn):
        return db().add_employer({**body.model_dump(), "source": "manual",
                                  "status": "active" if body.careers_url else "research"})

    @app.patch("/employers/{employer_id}", dependencies=A)
    def patch_employer(employer_id: int, body: EmployerPatch):
        s = db()
        fields = body.model_dump(exclude_none=True)
        if fields.get("careers_url") and "status" not in fields:
            fields["status"] = "active"
        r = s._update("employers", employer_id, fields)
        return r or notfound("employer")

    @app.get("/employers/{employer_id}/jobs", dependencies=A)
    def employer_jobs(employer_id: int):
        return db().jobs(employer_id)

    @app.post("/employers/{employer_id}/check", dependencies=A, status_code=202)
    def check_employer_ep(employer_id: int, bg: BackgroundTasks, force: bool = True):
        s = db()
        emp = next((e for e in s.employers() if e["id"] == employer_id), None) or notfound("employer")
        task_id = s.create_task("check_employer", employer_id)

        def work():
            from .role_match import check_employer
            try:
                r = check_employer(hooks.role_services(s, settings, True), emp, force=force)
                s.finish_task(task_id, "ok", r.__dict__)
            except Exception as exc:
                s.finish_task(task_id, "error", error=str(exc))
        bg.add_task(work)
        return {"task_id": task_id, "status": "running"}

    @app.post("/employers/import-companies-house", dependencies=A, status_code=202)
    def import_ch(bg: BackgroundTasks, sic: str | None = None, location: str | None = None, size: int = 100):
        from .role_seed import SEED_SIC_CODES
        if not settings.companies_house_api_key:
            raise HTTPException(400, "COMPANIES_HOUSE_API_KEY is not set")
        s = db()
        task_id = s.create_task("import_ch")
        sics = [x.strip() for x in (sic or ",".join(SEED_SIC_CODES)).split(",") if x.strip()]

        def work():
            from .ch_client import CompaniesHouseClient, employer_rows_from_search
            try:
                rows = employer_rows_from_search(CompaniesHouseClient(settings.companies_house_api_key)
                                                 .advanced_search(sics, location, size))
                for r in rows:
                    s.add_employer(r)
                s.finish_task(task_id, "ok", {"added": len(rows)})
            except Exception as exc:
                s.finish_task(task_id, "error", error=str(exc))
        bg.add_task(work)
        return {"task_id": task_id, "status": "running"}

    # -- lead orders (the simple flow) -------------------------------------------------------------
    from . import orders as O
    from .google_sheets import GoogleError, Sheets, auth_url, disconnect, exchange_code
    from .google_sheets import status as google_status

    def sheets_for(s: PgStore):
        if hooks.make_sheets is not None:
            return hooks.make_sheets(s, settings)
        return Sheets.from_store(s, settings)

    def fill(order_id: int, collect: bool = True) -> dict:
        s = db()
        return O.fill_order(s, settings, order_id, strict=strict(s), sheets=sheets_for(s), tools=order_tools(s),
                            collect=(lambda st, se, sid: hooks.collect_one(st, se, sid, st.create_run(sid))) if collect else None)

    def order_tools(s: PgStore):
        """Haiku, Companies House, Reed details, Brave, Hunter for checking leads (tests: hooks.make_tools)."""
        if hooks.make_tools is not None:
            return hooks.make_tools(s, settings)
        from .pipeline import make_tools
        return make_tools(s, settings)

    def order_out(s: PgStore, o: dict) -> dict:
        return {**o, "ready": O.ready_count(s, o, strict(s))}

    def new_order(body: dict) -> dict:
        try:
            return O.create_order(db(), **body)
        except ValueError as exc:
            if isinstance(exc, TerritoryTaken):
                raise
            raise HTTPException(422, str(exc))

    @app.get("/industries", dependencies=A)
    def industries():
        from .industries import INDUSTRIES as DEFS
        return [{"name": k, "keywords": v["search"], "in": v["in"], "out": v["out"]} for k, v in DEFS.items()]

    @app.get("/orders", dependencies=A)
    def orders_list(status: str | None = None):
        s = db()
        return [order_out(s, o) for o in O.list_orders(s, status)]

    @app.post("/orders", dependencies=A, status_code=201)
    def create_order_ep(body: OrderIn, bg: BackgroundTasks):
        from . import crm_lists
        if body.crm_mode and body.crm_mode not in crm_lists.MODES:
            raise HTTPException(422, f"crm_mode must be one of {crm_lists.MODES}")
        if body.crm_csv:
            try:
                crm_lists.parse(body.crm_csv)
            except ValueError as exc:
                raise HTTPException(422, f"CRM list: {exc}")
        o = new_order(body.model_dump(exclude={"crm_csv", "crm_mode"}))
        if body.crm_csv:
            o["crm_list"] = crm_lists.replace_list(db(), o["agency_id"], body.crm_csv, body.crm_mode)
        elif body.crm_mode:
            crm_lists.set_mode(db(), o["agency_id"], body.crm_mode)
        bg.add_task(fill, o["id"], True)
        return o

    @app.get("/agencies/{agency_id}/crm-list", dependencies=A)
    def crm_list_ep(agency_id: int):
        from . import crm_lists
        db().get_agency(agency_id) or notfound("agency")
        return crm_lists.summary(db(), agency_id)

    @app.put("/agencies/{agency_id}/crm-list", dependencies=A)
    def crm_list_upload(agency_id: int, body: CrmListIn):
        from . import crm_lists
        db().get_agency(agency_id) or notfound("agency")
        try:
            return crm_lists.replace_list(db(), agency_id, body.csv, body.mode)
        except ValueError as exc:
            raise HTTPException(422, str(exc))

    @app.patch("/agencies/{agency_id}/crm-list", dependencies=A)
    def crm_list_mode(agency_id: int, body: CrmModeIn):
        from . import crm_lists
        db().get_agency(agency_id) or notfound("agency")
        try:
            crm_lists.set_mode(db(), agency_id, body.mode)
        except ValueError as exc:
            raise HTTPException(422, str(exc))
        return crm_lists.summary(db(), agency_id)

    @app.delete("/agencies/{agency_id}/crm-list", dependencies=A)
    def crm_list_clear(agency_id: int):
        from . import crm_lists
        crm_lists.clear(db(), agency_id)
        return crm_lists.summary(db(), agency_id)

    @app.get("/orders/{order_id}", dependencies=A)
    def order_ep(order_id: int):
        s = db()
        return order_out(s, O.get_order(s, order_id) or notfound("order"))

    @app.patch("/orders/{order_id}", dependencies=A)
    def patch_order(order_id: int, body: OrderPatch):
        s = db()
        O.get_order(s, order_id) or notfound("order")
        try:
            return O.update_order(s, order_id, body.model_dump(exclude_none=True))
        except ValueError as exc:
            raise HTTPException(422, str(exc))

    @app.delete("/orders/{order_id}", dependencies=A, status_code=204)
    def delete_order_ep(order_id: int):
        O.delete_order(db(), order_id) or notfound("order")

    @app.post("/orders/{order_id}/send-more", dependencies=A, status_code=202)
    def send_more(order_id: int, bg: BackgroundTasks, collect: bool = True):
        s = db()
        o = O.get_order(s, order_id) or notfound("order")
        if o["status"] == "paused":
            raise HTTPException(409, "order is paused: resume it first")
        if o["status"] == "done":
            raise HTTPException(409, "order is complete: raise its number of leads first")
        bg.add_task(fill, order_id, collect)
        return {"order_id": order_id, "status": "running"}

    @app.get("/orders/{order_id}/leads.csv", dependencies=A)
    def order_csv(order_id: int):
        s = db()
        o = O.get_order(s, order_id) or notfound("order")
        rows = O.order_lead_rows(s, order_id)
        csv_text = to_csv(O.SHEET_HEADERS, [dict(zip(O.SHEET_HEADERS, O.sheet_row(r))) for r in rows])
        name = "".join(ch if ch.isalnum() else "-" for ch in f"{o['agency_name']}-{o['industry']}-{o['location']}").lower()
        return Response(content="\ufeff" + csv_text, media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f"attachment; filename={name}.csv"})

    # -- territories collected ahead of a sale -----------------------------------------------------
    @app.get("/territories", dependencies=A)
    def territories_ep():
        return O.territories(db())

    @app.post("/territories", dependencies=A, status_code=201)
    def watch_ep(body: TerritoryIn, bg: BackgroundTasks, run: bool = True):
        s = db()
        try:
            t = O.watch_territory(s, body.industry, body.location, body.radius_miles, body.keywords)
        except ValueError as exc:
            raise HTTPException(422, str(exc))
        if run:
            bg.add_task(hooks.collect_one, s, settings, t["search_id"], s.create_run(t["search_id"]))
        return t

    @app.post("/territories/{search_id}/preview", dependencies=A, status_code=202)
    def preview_ep(search_id: int, bg: BackgroundTasks):
        s = db()
        O.territory(s, search_id) or notfound("territory")
        task_id = s.create_task("territory_preview", search_id)

        def work():
            try:
                s.finish_task(task_id, "ok", O.preview_territory(s, search_id, order_tools(s)))
            except Exception as exc:
                s.finish_task(task_id, "error", error=str(exc))
        bg.add_task(work)
        return {"task_id": task_id, "status": "running"}

    @app.get("/leads", dependencies=A)
    def sent_leads_ep(order_id: int | None = None):
        return O.sent_leads(db(), order_id)

    @app.get("/agencies-overview", dependencies=A)
    def agencies_overview_ep():
        return O.agencies_overview(db())

    XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

    @app.get("/orders/{order_id}/leads.xlsx", dependencies=A)
    def order_xlsx(order_id: int):
        from .xlsx_export import workbook
        s = db()
        o = O.get_order(s, order_id) or notfound("order")
        rows = [O.sheet_row(r) for r in O.order_lead_rows(s, order_id)]
        name = "".join(ch if ch.isalnum() else "-" for ch in f"{o['agency_name']}-{o['industry']}-{o['location']}").lower()
        return Response(content=workbook(O.SHEET_HEADERS, rows, title="Hot leads"), media_type=XLSX,
                        headers={"Content-Disposition": f"attachment; filename={name}.xlsx"})

    # -- Google -------------------------------------------------------------------------------------
    @app.get("/google/status", dependencies=A)
    def google_status_ep():
        return {**google_status(db()), "client_set": bool(settings.google_client_id and settings.google_client_secret)}

    @app.get("/google/auth-url", dependencies=A)
    def google_auth_url(redirect_uri: str, state: str):
        if not settings.google_client_id:
            raise HTTPException(400, "GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET are not set on the server")
        return {"url": auth_url(settings.google_client_id, redirect_uri, state)}

    @app.post("/google/exchange", dependencies=A)
    def google_exchange(body: GoogleExchangeIn):
        if not (settings.google_client_id and settings.google_client_secret):
            raise HTTPException(400, "GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET are not set on the server")
        try:
            return exchange_code(db(), settings.google_client_id, settings.google_client_secret, body.code,
                                 body.redirect_uri)
        except GoogleError as exc:
            raise HTTPException(400, str(exc))

    @app.post("/google/disconnect", dependencies=A)
    def google_disconnect():
        disconnect(db())
        return {"connected": False}

    # -- AI assistant -------------------------------------------------------------------------------
    from . import assistant as AI

    def claude():
        if hooks.make_claude is not None:
            return hooks.make_claude(settings)
        if not settings.anthropic_api_key:
            raise HTTPException(400, "ANTHROPIC_API_KEY is not set on the server")
        import anthropic
        return anthropic.Anthropic(api_key=settings.anthropic_api_key, max_retries=3)

    def toolbox(s: PgStore, bg: BackgroundTasks) -> AI.Toolbox:
        return AI.Toolbox(store=s, settings=settings, strict=strict(s), new_order=new_order,
                          start_fill=lambda oid, collect=True: bg.add_task(fill, oid, collect),
                          order_out=lambda o: order_out(s, o), google=lambda: google_status(s),
                          start_collect=lambda sid: bg.add_task(hooks.collect_one, s, settings, sid, s.create_run(sid)),
                          tools_for=lambda: order_tools(s), start_project=lambda brief: new_project(brief))

    @app.post("/assistant/chat", dependencies=A)
    def assistant_chat(body: ChatIn, bg: BackgroundTasks):
        if not body.message.strip():
            raise HTTPException(422, "message is empty")
        s = db()
        return AI.chat(s, claude(), settings.assistant_model, toolbox(s, bg), body.message, body.conversation_id)

    @app.post("/assistant/start", dependencies=A, status_code=202)
    def assistant_start(body: ChatIn, bg: BackgroundTasks):
        """Long requests run in the background (phones drop a request that takes minutes); poll /tasks/{id}."""
        if not body.message.strip():
            raise HTTPException(422, "message is empty")
        s = db()
        client = claude()
        import uuid
        cid = body.conversation_id or uuid.uuid4().hex
        task_id = s.create_task("assistant")

        def work():
            try:
                s.finish_task(task_id, "ok", AI.chat(s, client, settings.assistant_model, toolbox(s, bg), body.message, cid))
            except Exception as exc:
                log.exception("assistant failed")
                s.finish_task(task_id, "error", error=str(exc)[:300])
        bg.add_task(work)
        return {"task_id": task_id, "conversation_id": cid, "status": "running"}

    # -- outreach (Smartlead) --------------------------------------------------------------------------
    from . import outreach as OR
    from .smartlead import Smartlead, SmartleadError

    def smartlead():
        if hooks.make_smartlead is not None:
            return hooks.make_smartlead(settings)
        try:
            return Smartlead(settings.smartlead_api_key, base_url=settings.smartlead_base_url)
        except SmartleadError as exc:
            raise HTTPException(400, str(exc))

    def sl_call(fn, *a, **kw):
        try:
            return fn(*a, **kw)
        except SmartleadError as exc:
            raise HTTPException(502, str(exc))
        except ValueError as exc:
            raise HTTPException(422, str(exc))

    @app.get("/outreach/stats", dependencies=A)
    def outreach_stats(refresh: bool = False):
        return sl_call(OR.stats, db(), smartlead(), refresh=refresh)

    @app.get("/outreach/campaigns", dependencies=A)
    def outreach_campaigns():
        return sl_call(OR.campaigns, db(), smartlead())

    @app.post("/outreach/campaigns/{campaign_id}/pause", dependencies=A)
    def outreach_pause(campaign_id: int):
        return sl_call(smartlead().set_status, campaign_id, "PAUSED") or {"ok": True}

    @app.post("/outreach/campaigns/{campaign_id}/resume", dependencies=A)
    def outreach_resume(campaign_id: int):
        return sl_call(smartlead().set_status, campaign_id, "START") or {"ok": True}

    @app.get("/outreach/queue", dependencies=A)
    def outreach_queue_ep():
        return OR.queue(db())

    @app.post("/outreach/queue/build", dependencies=A)
    def outreach_build(limit: int | None = None):
        return sl_call(OR.build_queue, db(), settings, smartlead(), limit=limit)

    @app.post("/outreach/queue/push", dependencies=A)
    def outreach_push(body: PushIn):
        return sl_call(OR.push, db(), smartlead(), body.ids)

    @app.post("/outreach/queue/test-push", dependencies=A)
    def outreach_test_push(row_id: int | None = None):
        return sl_call(OR.test_push, db(), settings, smartlead(), row_id)

    @app.post("/outreach/queue/{row_id}", dependencies=A)
    def outreach_row(row_id: int, body: RowActionIn):
        s = db()
        OR.row(s, row_id) or notfound("queue row")
        try:
            if body.action == "remove":
                OR.remove(s, row_id)
            elif body.action == "swap":
                OR.swap_lead(s, row_id, body.which)
            elif body.action in OR.ROLES:
                OR.move(s, row_id, body.action)
            else:
                raise ValueError("action must be remove, swap, leaders or sales")
        except ValueError as exc:
            raise HTTPException(422, str(exc))
        return OR.row(s, row_id)

    @app.get("/outreach/queue/{row_id}/preview", dependencies=A)
    def outreach_preview(row_id: int):
        s = db()
        r = OR.row(s, row_id) or notfound("queue row")
        sl = smartlead() if settings.smartlead_api_key or hooks.make_smartlead else None
        return sl_call(OR.preview, s, sl, r)

    @app.get("/outreach/replies", dependencies=A)
    def outreach_replies_ep():
        return OR.replies(db())

    @app.post("/outreach/replies/sync", dependencies=A)
    def outreach_sync():
        return sl_call(OR.sync_replies, db(), smartlead())

    @app.post("/outreach/replies/{reply_id}/categorise", dependencies=A)
    def outreach_categorise(reply_id: int, body: CategoryIn):
        return sl_call(OR.categorise, db(), smartlead(), reply_id, body.category)

    @app.get("/outreach/health", dependencies=A)
    def outreach_health(refresh: bool = False):
        s = db()
        if not refresh:
            cached = s.cache_get("outreach:health", max_age_days=15 / 1440)
            if cached:
                return cached
        return sl_call(OR.health, s, smartlead())

    @app.get("/outreach/settings", dependencies=A)
    def outreach_settings_get():
        s = db()
        cids = OR.campaign_ids(s)
        return {"leaders_campaign_id": cids["leaders"], "sales_campaign_id": cids["sales"],
                "daily_push_limit": int(OR.setting(s, OR.S_LIMIT, 50)), "auto_push": bool(OR.setting(s, OR.S_AUTO, False)),
                "send_window": OR.setting(s, OR.S_WINDOW, ""), "key_set": bool(settings.smartlead_api_key),
                "owner_test_email": settings.owner_test_email or None}

    @app.patch("/outreach/settings", dependencies=A)
    def outreach_settings_patch(body: dict[str, Any]):
        s = db()
        keys = {"leaders_campaign_id": OR.S_LEADERS, "sales_campaign_id": OR.S_SALES, "daily_push_limit": OR.S_LIMIT,
                "auto_push": OR.S_AUTO, "send_window": OR.S_WINDOW}
        bad = set(body) - set(keys)
        if bad:
            raise HTTPException(422, f"unknown settings {sorted(bad)}")
        if "daily_push_limit" in body and not 1 <= int(body["daily_push_limit"]) <= 400:
            raise HTTPException(422, "daily push limit must be 1-400")
        for k, v in body.items():
            s.set_setting(keys[k], v)
        s.q("delete from http_cache where key = 'outreach:stats'")
        return outreach_settings_get()

    # -- personalising the leads already in the Smartlead campaigns ------------------------------------
    from . import personalise as PZ

    @app.get("/outreach/personalise", dependencies=A)
    def personalise_summary():
        return PZ.summary(db())

    @app.post("/outreach/personalise/run", dependencies=A, status_code=202)
    def personalise_run(bg: BackgroundTasks, dry_run: bool = False):
        """Sync both campaigns' leads, check each agency and its niche, write the hot leads. Takes a while: poll GET."""
        s = db()
        sl = smartlead()
        tools = order_tools(s)
        task_id = s.create_task("personalise")

        def work():
            try:
                s.finish_task(task_id, "ok", PZ.run(s, settings, sl, client=tools.claude, geocoder=tools.geocoder,
                                                    ch=tools.ch, dry_run=dry_run))
            except Exception as exc:
                log.exception("personalise failed")
                s.finish_task(task_id, "error", error=str(exc)[:300])
        bg.add_task(work)
        return {"task_id": task_id, "status": "running"}

    @app.get("/outreach/personalise/leads", dependencies=A)
    def personalise_leads(state: str | None = None, limit: int = 200):
        return PZ.leads(db(), state, min(limit, 2000))

    @app.get("/outreach/personalise/held.csv", dependencies=A)
    def personalise_held_csv():
        return Response(PZ.held_csv(db()), media_type="text/csv",
                        headers={"Content-Disposition": 'attachment; filename="leads-to-remove.csv"'})

    @app.get("/outreach/personalise/{campaign_id}/{lead_id}/preview", dependencies=A)
    def personalise_preview(campaign_id: int, lead_id: int):
        return sl_call(PZ.preview, db(), smartlead(), campaign_id, lead_id)

    @app.post("/outreach/import-contacts", dependencies=A)
    def outreach_import_contacts(body: ProspectsIn):
        from . import prospects
        try:
            return prospects.import_contacts(db(), body.csv, trust_unlabelled=body.trust_unlabelled)
        except ValueError as exc:
            raise HTTPException(422, str(exc))

    @app.post("/outreach/test-connection", dependencies=A)
    def outreach_test_connection():
        try:
            return smartlead().test()
        except SmartleadError as exc:
            return {"ok": False, "error": str(exc)}

    # -- project agent (research briefs) -----------------------------------------------------------------
    from . import projects as P

    def research_for(s: PgStore):
        if hooks.make_research is not None:
            return hooks.make_research(s, settings)
        from .pipeline import make_reed
        from .qualify import make_client
        from .research_tools import Research
        return Research(s, settings, claude=make_client(settings), reed=make_reed(settings))

    def start_project(pid: int, message: str | None = None) -> None:
        s = db()
        client = claude()
        work = lambda: P.run(s, client, settings.assistant_model, research_for(s), pid, message)
        if hooks.run_in_thread:
            import threading
            threading.Thread(target=work, name=f"project-{pid}", daemon=True).start()
        else:
            work()

    def new_project(brief: str, title: str | None = None) -> dict:
        s = db()
        claude()   # fail early (400) when the key is missing
        try:
            p = P.create(s, brief, title)
        except ValueError as exc:
            raise HTTPException(422, str(exc))
        start_project(p["id"])
        return P.get(s, p["id"])

    @app.post("/projects", dependencies=A, status_code=202)
    def create_project(body: ProjectIn):
        return new_project(body.brief, body.title)

    @app.get("/projects", dependencies=A)
    def projects_list():
        return P.list_all(db())

    @app.get("/projects/{project_id}", dependencies=A)
    def project_ep(project_id: int):
        return P.get(db(), project_id) or notfound("project")

    @app.post("/projects/{project_id}/message", dependencies=A, status_code=202)
    def project_message(project_id: int, body: ProjectMessageIn):
        s = db()
        p = P.get(s, project_id) or notfound("project")
        if p["status"] in ("running", "queued"):
            raise HTTPException(409, "the project is still running: wait for it, or cancel it first")
        start_project(project_id, body.message.strip() or "Carry on where you stopped and submit the results.")
        return P.get(s, project_id)

    @app.post("/projects/{project_id}/use-as-style", dependencies=A)
    def project_style(project_id: int):
        try:
            return P.save_style(db(), project_id)
        except ValueError as exc:
            raise HTTPException(409, str(exc))

    @app.get("/projects-style", dependencies=A)
    def project_style_get():
        return db().setting(P.STYLE_SETTING) or {}

    @app.post("/projects/{project_id}/cancel", dependencies=A)
    def project_cancel(project_id: int):
        s = db()
        P.get(s, project_id) or notfound("project")
        s.q("update projects set status = 'cancelled', finished_at = now() where id = %s and status in ('running', 'queued')",
            (project_id,))
        return P.get(s, project_id)

    @app.get("/projects/{project_id}/result.csv", dependencies=A)
    def project_csv(project_id: int):
        p = P.get(db(), project_id) or notfound("project")
        if not p.get("result"):
            raise HTTPException(404, "no results yet")
        name = "".join(ch if ch.isalnum() else "-" for ch in p["title"][:50]).strip("-").lower() or f"project-{project_id}"
        return Response(content="\ufeff" + P.result_csv(p["result"]), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f"attachment; filename={name}.csv"})

    @app.get("/projects/{project_id}/result.xlsx", dependencies=A)
    def project_xlsx(project_id: int, notes: bool = False):
        """Client version by default (leads + summary only). notes=true adds the email and the private note."""
        from .xlsx_export import workbook
        p = P.get(db(), project_id) or notfound("project")
        r = p.get("result") or notfound("results")
        email = (f"Subject: {r.get('email_subject')}\n\n" if r.get("email_subject") else "") + (r.get("email_body") or "")
        name = "".join(ch if ch.isalnum() else "-" for ch in p["title"][:50]).strip("-").lower() or f"project-{project_id}"
        return Response(content=workbook(r.get("columns") or [], r.get("rows") or [], title="Leads",
                                         summary=r.get("summary_lines"), header_lines=r.get("header_lines"),
                                         extra_sheets={"Email": email, "Note (private)": r.get("note")} if notes else None),
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": f"attachment; filename={name}{'-with-notes' if notes else ''}.xlsx"})

    @app.get("/assistant/conversations/{conversation_id}", dependencies=A)
    def assistant_history(conversation_id: str):
        return {"conversation_id": conversation_id, "messages": AI.visible_history(db(), conversation_id)}

    return app


def _make_default_app() -> FastAPI:
    settings = get_settings()
    setup_logging(settings)
    return create_app(settings)


class _LazyApp:
    """`uvicorn app.main:app` builds the real app on first use, so importing this module needs no env."""

    def __init__(self):
        self._app: FastAPI | None = None

    async def __call__(self, scope, receive, send):
        if self._app is None:
            self._app = _make_default_app()
        await self._app(scope, receive, send)


app = _LazyApp()
